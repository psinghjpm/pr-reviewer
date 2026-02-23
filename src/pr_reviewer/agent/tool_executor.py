"""Dispatch agent tool calls to context engine and platform adapters."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import structlog

from pr_reviewer.context.dependency_tracer import DependencyTracer
from pr_reviewer.context.git_history import GitHistory
from pr_reviewer.context.symbol_search import SymbolSearch
from pr_reviewer.context.test_finder import RelatedTestFinder
from pr_reviewer.models import AgentSession, ReviewCategory, ReviewFinding, Severity, ToolResult

if TYPE_CHECKING:
    from pr_reviewer.platforms.base import PlatformAdapter

logger = structlog.get_logger(__name__)

MAX_CONTENT_LENGTH = 12_000  # chars per tool result


class ToolExecutor:
    """Execute agent tool calls and return ToolResult objects.

    Holds references to the platform adapter and context engine helpers.
    All file content is truncated to MAX_CONTENT_LENGTH to stay within
    the Claude context budget.
    """

    def __init__(
        self,
        adapter: "PlatformAdapter",
        session: AgentSession,
        max_content_length: int = MAX_CONTENT_LENGTH,
    ) -> None:
        self._adapter = adapter
        self._session = session
        self._max_len = max_content_length

        head_sha = session.pr_metadata.head_sha
        self._ref = head_sha

        # Context helpers (initialized lazily / on first use)
        self._repo_files: list[str] | None = None
        self._tracer: DependencyTracer | None = None
        self._symbol_search: SymbolSearch | None = None
        self._test_finder: RelatedTestFinder | None = None
        self._git_history = GitHistory(adapter)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def execute(self, tool_name: str, tool_input: dict[str, Any], tool_use_id: str) -> ToolResult:
        """Route tool_name to the appropriate handler."""
        logger.debug("tool_call", tool=tool_name, input=tool_input)
        try:
            handler = getattr(self, f"_tool_{tool_name}", None)
            if handler is None:
                return ToolResult(
                    tool_use_id=tool_use_id,
                    content=f"Unknown tool: {tool_name}",
                    is_error=True,
                )
            content = handler(**tool_input)
            return ToolResult(tool_use_id=tool_use_id, content=self._truncate(content))
        except Exception as exc:
            logger.exception("tool_error", tool=tool_name, error=str(exc))
            return ToolResult(
                tool_use_id=tool_use_id,
                content=f"Tool error ({tool_name}): {exc}",
                is_error=True,
            )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _truncate(self, text: str) -> str:
        if len(text) > self._max_len:
            return text[: self._max_len] + f"\n\n[... truncated at {self._max_len} chars ...]"
        return text

    def _get_repo_files(self) -> list[str]:
        if self._repo_files is None:
            self._repo_files = self._adapter.list_repo_files(self._ref, "**/*.py")
        return self._repo_files

    def _get_tracer(self) -> DependencyTracer:
        if self._tracer is None:
            self._tracer = DependencyTracer(
                adapter=self._adapter,
                ref=self._ref,
                repo_files=self._get_repo_files(),
            )
            # Seed with changed files
            changed = [fd.path for fd in self._session.diff]
            self._tracer.build_graph(changed)
        return self._tracer

    def _get_symbol_search(self) -> SymbolSearch:
        if self._symbol_search is None:
            self._symbol_search = SymbolSearch(
                adapter=self._adapter,
                ref=self._ref,
                repo_files=self._get_repo_files(),
            )
        return self._symbol_search

    def _get_test_finder(self) -> RelatedTestFinder:
        if self._test_finder is None:
            self._test_finder = RelatedTestFinder(
                adapter=self._adapter,
                ref=self._ref,
                repo_files=self._get_repo_files(),
                tracer=self._get_tracer(),
            )
        return self._test_finder

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    def _tool_fetch_full_file(self, path: str, ref: str | None = None) -> str:
        effective_ref = ref or self._ref
        content = self._adapter.get_file_content(path, effective_ref)
        if not content:
            return f"File not found or empty: {path} @ {effective_ref}"
        # Store in session context for deduplication
        self._session.context_fetched[path] = content[:200]  # store summary only
        return f"# {path}\n\n```python\n{content}\n```"

    def _tool_search_symbol(self, name: str, kind: str = "function") -> str:
        ss = self._get_symbol_search()
        info = ss.find_symbol(name, kind)
        if info is None:
            return f"Symbol `{name}` ({kind}) not found in the repository."

        lines = [
            f"## Symbol: `{name}` ({kind})",
            f"**Defined in:** `{info.defined_in}` line {info.defined_at_line}",
            "",
        ]
        if info.call_sites:
            lines.append(f"**Call sites** ({len(info.call_sites)}):")
            for cs in info.call_sites[:30]:  # cap at 30
                lines.append(f"  - `{cs.file}:{cs.line}` — {cs.snippet}")
        else:
            lines.append("No call sites found.")
        return "\n".join(lines)

    def _tool_get_file_dependencies(self, path: str, depth: int = 2) -> str:
        tracer = self._get_tracer()
        # Ensure the file is parsed
        tracer.build_graph([path])
        node = tracer.get_dependencies(path, depth=depth)

        lines = [
            f"## Dependencies for `{path}`",
            "",
            f"**Imports ({len(node.imports)} files):**",
        ]
        for imp in node.imports[:20]:
            lines.append(f"  - {imp}")
        if len(node.imports) > 20:
            lines.append(f"  ... and {len(node.imports) - 20} more")

        lines += [
            "",
            f"**Imported by ({len(node.imported_by)} files — blast radius):**",
        ]
        for imp_by in node.imported_by[:20]:
            lines.append(f"  - {imp_by}")
        if len(node.imported_by) > 20:
            lines.append(f"  ... and {len(node.imported_by) - 20} more")

        lines += [
            "",
            f"**Defined symbols:** {', '.join(node.defined_symbols[:30]) or 'none'}",
        ]
        return "\n".join(lines)

    def _tool_get_related_tests(self, source_path: str) -> str:
        finder = self._get_test_finder()
        test_contents = finder.fetch_test_content(source_path)

        if not test_contents:
            paths = finder.find_test_files(source_path)
            if not paths:
                return f"No test files found for `{source_path}`."
            return f"Found test files but couldn't fetch content: {', '.join(paths)}"

        parts: list[str] = [f"## Test files for `{source_path}`\n"]
        for test_path, content in test_contents.items():
            parts.append(f"### {test_path}\n```python\n{content}\n```\n")
        return "\n".join(parts)

    def _tool_search_codebase(self, pattern: str, file_pattern: str = "*.py") -> str:
        results = self._adapter.search_repo_code(pattern, file_pattern)
        if not results:
            return f"No results found for pattern: `{pattern}`"

        lines = [f"## Search results for `{pattern}` ({len(results)} matches)\n"]
        for r in results:
            lines.append(f"- `{r['path']}:{r.get('line', '?')}` — {r.get('snippet', '')}")
        return "\n".join(lines)

    def _tool_list_directory(self, path: str) -> str:
        # Use list_repo_files with an appropriate glob
        if path in (".", "", "/"):
            glob = "**/*"
        else:
            path = path.rstrip("/")
            glob = f"{path}/**/*"

        all_files = self._adapter.list_repo_files(self._ref, glob)
        # Show only immediate children
        immediate: set[str] = set()
        for f in all_files:
            relative = f[len(path) + 1:] if f.startswith(path + "/") else f
            top = relative.split("/")[0]
            immediate.add(top)

        if not immediate:
            return f"Directory `{path}` is empty or does not exist."

        lines = [f"## Directory: `{path}`\n"]
        for item in sorted(immediate):
            lines.append(f"  {item}")
        return "\n".join(lines)

    def _tool_get_git_history(self, path: str, limit: int = 10) -> str:
        return self._git_history.get_history(path, limit=limit)

    def _tool_get_pr_history_comments(self) -> str:
        pr_id = self._session.pr_metadata.pr_id
        comments = self._adapter.get_existing_comments(pr_id)
        if not comments:
            return "No existing comments on this PR."

        lines = [f"## Existing PR comments ({len(comments)})\n"]
        for c in comments[:50]:  # cap at 50
            path_info = f"`{c['path']}:{c.get('line', '?')}`" if c.get("path") else "(summary)"
            lines.append(f"- {path_info}: {c['body'][:100]}")
        return "\n".join(lines)

    def handle_emit_finding(self, tool_input: dict[str, Any]) -> ReviewFinding:
        """Parse and record a ReviewFinding from emit_finding tool call."""
        finding = ReviewFinding(
            file=tool_input["file"],
            line_start=tool_input["line_start"],
            line_end=tool_input["line_end"],
            severity=Severity(tool_input["severity"]),
            category=ReviewCategory(tool_input["category"]),
            message=tool_input["message"],
            suggestion=tool_input.get("suggestion", ""),
            confidence=float(tool_input.get("confidence", 0.8)),
        )
        self._session.findings.append(finding)
        logger.info(
            "finding_emitted",
            severity=finding.severity,
            file=finding.file,
            line=finding.line_start,
        )
        return finding
