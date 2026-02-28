"""Core Claude agentic PR review loop."""

from __future__ import annotations

import json
import textwrap
from typing import Any

import anthropic
import structlog

from pr_reviewer.agent.tool_definitions import AGENT_TOOLS
from pr_reviewer.agent.tool_executor import ToolExecutor
from pr_reviewer.models import AgentSession, FileDiff, PRMetadata, PRSummary, RepoContext, Severity, TestStub
from pr_reviewer.platforms.base import PlatformAdapter
from pr_reviewer.utils.diff_parser import diff_summary

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_BASE = textwrap.dedent("""
You are an expert code reviewer powered by Claude. You perform deep, context-aware
pull request reviews that rival CodeRabbit, Qodo, Greptile, and Graphite.

## Review Passes (execute in order)

### Pass 1 — INTENT
Read the PR title, description, and diff overview. Understand:
- What problem is this PR solving?
- What is the intended change?
- Is the implementation consistent with the stated intent?

### Pass 2 — CONTEXT
Use tools aggressively to gather full context before drawing conclusions:
- `fetch_full_file` for every changed file (don't rely only on diff hunks)
- `get_file_dependencies` to understand import blast-radius
- `search_symbol` for any non-obvious function/class referenced in changes
- `get_related_tests` for each changed source file
- `get_git_history` to understand prior change patterns
- `get_pr_history_comments` to avoid duplicate findings

### Pass 3 — LOGIC & BUGS
Carefully analyze:
- Logic errors, off-by-one errors, incorrect conditionals
- Edge cases: empty collections, None values, integer overflow
- Race conditions, shared mutable state
- Incorrect error handling, silent failures
- API contract violations (wrong arguments, wrong return types)

### Pass 4 — SECURITY
Check for:
- Injection vulnerabilities (SQL, command, template, LDAP)
- Authentication/authorization bypass
- Sensitive data exposure (secrets in logs, unmasked credentials)
- SSRF, path traversal, unsafe deserialization
- Missing input validation at trust boundaries

### Pass 5 — TESTS
- Identify new public functions/classes without test coverage
- Check existing tests for correctness after the change
- Generate pytest test stubs for untested new functionality

## Tool Usage Guidelines
- Use `emit_finding` for EVERY issue found (one call per distinct finding)
- Only emit findings with confidence >= 0.5
- Prefer HIGH/CRITICAL sparingly — reserve for real bugs and security issues
- Always include a concrete `suggestion` with corrected code when possible
- Do NOT emit style nitpicks unless they are significant maintainability issues

## Output Format
After all tool calls, you will be asked to produce a JSON summary. Wait for the
FINAL_SUMMARY_PROMPT before writing JSON.
""").strip()

FINAL_SUMMARY_PROMPT = textwrap.dedent("""
You have completed the code review. Now produce a structured JSON summary of the
entire review. Output ONLY valid JSON (no markdown fences), with this exact shape:

{
  "overview": "<2-3 sentence summary of the PR and its quality>",
  "intent": "<one sentence: what the PR is trying to accomplish>",
  "risk_level": "<CRITICAL|HIGH|MEDIUM|LOW|INFO>",
  "findings_by_severity": {
    "CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0, "INFO": 0
  },
  "key_concerns": ["<top concern 1>", "<top concern 2>", "..."],
  "test_stubs": [
    {
      "function_name": "<fn>",
      "test_name": "<test_fn_name>",
      "source_file": "<path>",
      "stub_code": "<pytest stub code>",
      "description": "<what this test should verify>"
    }
  ],
  "suggestions": ["<general suggestion 1>", "..."]
}
""").strip()


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------


def build_system_prompt(repo_context: RepoContext | None = None) -> str:
    """Return the system prompt, optionally prepended with repo context."""
    if repo_context is None:
        return _SYSTEM_PROMPT_BASE

    lines = [
        "## REPOSITORY CONTEXT\n",
        "Use this throughout all analysis passes to produce convention-aware findings.\n",
    ]
    if repo_context.languages or repo_context.frameworks:
        stack = ", ".join(repo_context.languages + repo_context.frameworks)
        lines.append(f"**Tech Stack:** {stack}")
    if repo_context.build_tool:
        lines.append(f"**Build Tool:** {repo_context.build_tool}")
    if repo_context.architecture_pattern:
        notes = f" — {repo_context.architecture_notes}" if repo_context.architecture_notes else ""
        lines.append(f"**Architecture:** {repo_context.architecture_pattern}{notes}")
    if repo_context.naming_conventions:
        lines.append(f"**Naming:** {repo_context.naming_conventions}")
    if repo_context.error_handling_pattern:
        lines.append(f"**Error Handling:** {repo_context.error_handling_pattern}")
    if repo_context.security_sensitive_paths:
        paths = ", ".join(repo_context.security_sensitive_paths)
        lines.append(f"**Security-Sensitive Paths:** {paths} ← flag any PR changes here")
    if repo_context.test_framework:
        lines.append(f"**Test Framework:** {repo_context.test_framework} | {repo_context.test_structure}")
    if repo_context.test_conventions:
        lines.append(f"**Test Conventions:** {', '.join(repo_context.test_conventions)}")
    if repo_context.review_hints:
        lines.append("**Review Hints (known pitfalls):**")
        lines.extend(f"  - {h}" for h in repo_context.review_hints)
    if repo_context.additional_context:
        lines.append(f"**Additional Context:** {repo_context.additional_context}")

    return "\n".join(lines) + "\n\n---\n\n" + _SYSTEM_PROMPT_BASE


# ---------------------------------------------------------------------------
# Message builders
# ---------------------------------------------------------------------------

def _build_diff_context(diff: list[FileDiff]) -> str:
    """Format the diff for the initial message."""
    parts = [diff_summary(diff), ""]
    for fd in diff:
        if not fd.hunks:
            continue
        parts.append(f"### {fd.path} (+{fd.additions}/-{fd.deletions})")
        for hunk in fd.hunks:
            parts.append(
                f"@@ -{hunk.source_start},{hunk.source_length} "
                f"+{hunk.target_start},{hunk.target_length} @@"
                f" {hunk.section_header}"
            )
            for line in hunk.lines[:200]:  # cap per hunk
                parts.append(f"{line.line_type}{line.value.rstrip()}")
        parts.append("")
    return "\n".join(parts)


def _build_initial_messages(pr: PRMetadata, diff: list[FileDiff]) -> list[dict]:
    diff_text = _build_diff_context(diff)
    user_content = textwrap.dedent(f"""
        Please review the following pull request.

        ## PR Metadata
        - **Title:** {pr.title}
        - **Author:** {pr.author}
        - **Source branch:** {pr.source_branch} → {pr.target_branch}
        - **Repo:** {pr.repo_full_name}
        - **URL:** {pr.url}

        ## Description
        {pr.description or '_(no description provided)_'}

        ## Diff Overview
        {diff_text}

        Begin your review now. Use the provided tools to gather full context before
        emitting findings. Follow all five review passes described in your system prompt.
    """).strip()

    return [{"role": "user", "content": user_content}]


# ---------------------------------------------------------------------------
# Core agentic loop
# ---------------------------------------------------------------------------


class PRReviewer:
    """Orchestrate the Claude agentic review loop."""

    def __init__(
        self,
        adapter: PlatformAdapter,
        api_key: str,
        model: str = "claude-sonnet-4-6",
        max_tool_calls: int = 60,
        max_content_length: int = 12_000,
        repo_context: RepoContext | None = None,
    ) -> None:
        self._adapter = adapter
        self._client = anthropic.Anthropic(api_key=api_key)
        self._model = model
        self._max_tool_calls = max_tool_calls
        self._max_content_length = max_content_length
        self._repo_context = repo_context

    def review(self, pr_id: int | str) -> AgentSession:
        """Run the full agentic review for *pr_id*. Returns a completed AgentSession."""
        logger.info("review_start", pr_id=pr_id, model=self._model)

        # Fetch PR data
        pr_metadata = self._adapter.get_pr_metadata(pr_id)
        diff = self._adapter.get_pr_diff(pr_id)

        session = AgentSession(
            pr_metadata=pr_metadata,
            diff=diff,
            max_tool_calls=self._max_tool_calls,
        )
        executor = ToolExecutor(
            adapter=self._adapter,
            session=session,
            max_content_length=self._max_content_length,
        )

        messages = _build_initial_messages(pr_metadata, diff)

        # ---------------------------------------------------------------
        # Agentic loop
        # ---------------------------------------------------------------
        while session.tool_call_count < session.max_tool_calls:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=8192,
                system=build_system_prompt(self._repo_context),
                tools=AGENT_TOOLS,
                messages=messages,
            )

            # Append assistant response to history
            messages.append({"role": "assistant", "content": response.content})

            if response.stop_reason == "end_turn":
                logger.info("agent_end_turn", tool_calls_used=session.tool_call_count)
                break

            if response.stop_reason != "tool_use":
                logger.warning("unexpected_stop_reason", reason=response.stop_reason)
                break

            # Process tool calls
            tool_results: list[dict] = []
            for block in response.content:
                if block.type != "tool_use":
                    continue

                session.tool_call_count += 1

                if block.name == "emit_finding":
                    finding = executor.handle_emit_finding(block.input)
                    result_content = f"Finding recorded: {finding.severity} [{finding.category}] at {finding.file}:{finding.line_start}"
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_content,
                        }
                    )
                else:
                    tool_result = executor.execute(block.name, block.input, block.id)
                    tool_results.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": tool_result.content,
                            **({"is_error": True} if tool_result.is_error else {}),
                        }
                    )

            if not tool_results:
                break

            messages.append({"role": "user", "content": tool_results})

            if session.tool_call_count >= session.max_tool_calls:
                logger.warning("tool_call_budget_exhausted", count=session.tool_call_count)
                break

        # ---------------------------------------------------------------
        # Final summary pass
        # ---------------------------------------------------------------
        session.summary = self._generate_summary(session, messages)
        logger.info(
            "review_complete",
            findings=len(session.findings),
            risk=session.summary.risk_level if session.summary else "unknown",
        )
        return session

    def _generate_summary(
        self, session: AgentSession, messages: list[dict]
    ) -> PRSummary | None:
        """Ask Claude to produce the structured JSON summary."""
        summary_messages = messages + [
            {"role": "user", "content": FINAL_SUMMARY_PROMPT}
        ]

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                system=build_system_prompt(self._repo_context),
                messages=summary_messages,
            )
            raw = ""
            for block in response.content:
                if hasattr(block, "text"):
                    raw += block.text

            # Strip markdown fences if present
            raw = raw.strip()
            if raw.startswith("```"):
                raw = "\n".join(raw.split("\n")[1:])
            if raw.endswith("```"):
                raw = "\n".join(raw.split("\n")[:-1])

            data = json.loads(raw.strip())

            # Compute findings_by_severity from actual findings
            sev_counts: dict[str, int] = {s.value: 0 for s in Severity}
            for f in session.findings:
                sev_counts[f.severity.value] = sev_counts.get(f.severity.value, 0) + 1
            data["findings_by_severity"] = sev_counts

            # Parse test stubs
            stubs = [
                TestStub(**ts) for ts in data.get("test_stubs", []) if isinstance(ts, dict)
            ]

            return PRSummary(
                overview=data.get("overview", ""),
                intent=data.get("intent", ""),
                risk_level=Severity(data.get("risk_level", "MEDIUM")),
                findings_by_severity=sev_counts,
                key_concerns=data.get("key_concerns", []),
                test_stubs=stubs,
                suggestions=data.get("suggestions", []),
            )

        except Exception as exc:
            logger.exception("summary_generation_failed", error=str(exc))
            # Fallback minimal summary
            sev_counts = {s.value: 0 for s in Severity}
            for f in session.findings:
                sev_counts[f.severity.value] = sev_counts.get(f.severity.value, 0) + 1
            max_sev = max(
                session.findings, key=lambda f: f.severity, default=None
            )
            return PRSummary(
                overview="Review completed. See inline comments for details.",
                intent="",
                risk_level=max_sev.severity if max_sev else Severity.INFO,
                findings_by_severity=sev_counts,
                key_concerns=[],
                test_stubs=[],
                suggestions=[],
            )
