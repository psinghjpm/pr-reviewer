# PRGenie — Architecture Reference

## Overview

PRGenie is an agentic code review system built on Claude. It operates at two points in
the development lifecycle — **before a commit is made** (local, via MCP) and **after a
PR is opened** (remote, via Claude Code skill or standalone CLI). All three modes share
the same 5-pass analysis framework but differ in where they run, what they cost, and
what infrastructure they require.

```
Developer writes code
        │
        ▼
   git add (stage)
        │
        ▼
┌───────────────────────────┐
│  MCP Local Reviewer       │  ← catches issues before they leave the laptop
│  IDE chat · no API key    │
└───────────────────────────┘
        │
        ▼
   git commit → git push → PR opened
                                │
               ┌────────────────┼────────────────┐
               ▼                ▼                ▼
        /pr-review         Standalone CLI     EKS webhook
        skill               (API key)         (auto, K8s)
        (Claude Pro)
               └────────────────┼────────────────┘
                                ▼
                   Inline comments + summary
                   posted to GitHub / Bitbucket
```

---

## Deployment Modes

### Mode 1 — MCP Local Pre-Commit Reviewer

**What it is:** A FastMCP stdio server (`mcp_server/server.py`) that any MCP-compatible
IDE registers as a local tool provider. When a developer stages changes and asks their IDE
to "review my staged changes", the IDE's own LLM calls the MCP tools to gather context
and runs the 5-pass review entirely in the chat window.

**How it works:**
1. Developer stages changes: `git add src/foo.py`
2. In the IDE: `"review my staged changes"`
3. The IDE calls `get_staged_changes` → server runs `git diff --cached` + fetches full file contents
4. IDE LLM receives the full diff + file content and performs the structured review
5. Findings appear as chat output with file:line references

**MCP tools exposed:**

| Tool | Arguments | What it returns |
|---|---|---|
| `list_staged_files` | `workspace_path?` | File-level overview: path, add/modify/delete status |
| `get_recent_commits` | `workspace_path?`, `count?` (1–50) | Recent commit messages for intent context |
| `get_staged_changes` | `workspace_path?`, `include_unstaged?` | Full diff + complete file content for each changed file |

**Key design decisions:**
- Transport: stdio (IDE spawns `pr-reviewer-mcp` as a subprocess)
- `_run()` checks `returncode` and returns `""` on git failure — callers degrade gracefully
- `count` clamped: `max(1, min(count, 50))` — prevents runaway context
- No API key required — the IDE's LLM does all the reasoning

**Compatible IDEs:** Claude Code, Cline (VSCode), Roo Code (VSCode), GitHub Copilot (VSCode 1.99+)

---

### Mode 2 — Claude Code Skill (`/pr-review`)

This is the flagship mode. It runs entirely within a Claude Code session, using Claude's
own reasoning to orchestrate `gh` CLI calls, analyze the code, and post results back to
GitHub or Bitbucket.

**Component map:**

```
~/.claude/skills/pr-review/
├── SKILL.md          ← skill prompt (all review logic lives here)
└── post_review.py    ← GitHub PR comment poster

~/.claude/skills/pr-feedback/
└── SKILL.md          ← feedback harvesting skill

Repo (local or remote):
└── .pr-reviewer/
    ├── repo_context.json      ← onboarded conventions (from /repo-onboard)
    ├── suppressions.json      ← team-curated false-positive suppressions
    ├── context/               ← external tool drop-zone (Snyk, SonarQube, JIRA)
    └── adapters/              ← adapter scripts for each external tool
```

**Review flow (6 phases):**

```
Phase 0: Load context
  ├── repo_context.json  → naming conventions, architecture notes, security paths
  ├── suppressions.json  → active suppression rules (CRITICAL/SECURITY never suppressed)
  └── context/*.json     → external tool findings (Snyk, SonarQube, JIRA)

Phase 1: Fetch PR metadata + diff
  └── gh pr view + gh pr diff

Phase 2: Gather deep context (per changed file)
  ├── Full file content at HEAD SHA
  ├── Related test files
  ├── Recent git history (last 5 commits for that file)
  └── Existing PR inline comments (for deduplication)

Phase 3: 5-pass analysis
  ├── Pass 1: INTENT    — does the code do what the PR description says?
  ├── Pass 2: LOGIC     — bugs, null dereferences, edge cases, race conditions
  ├── Pass 3: SECURITY  — injections, path traversal, auth bypass, data leakage
  ├── Pass 4: QUALITY   — conventions, dead code, types, performance
  └── Pass 5: TESTS     — coverage gaps, test stubs for new public functions

Phase 4: Compile findings
  ├── Filter confidence < 0.5
  ├── Deduplicate against existing PR comments
  ├── Apply suppressions (never suppress CRITICAL or SECURITY)
  └── Cap at 30 inline findings

Phase 5: Post comments
  ├── Write /tmp/pr_comments.json + /tmp/pr_summary.md
  └── Call post_review.py (4 API calls total)

Phase 6: Terminal report
```

**post_review.py — how inline comments are posted:**

GitHub's batch review API requires each comment to specify a `position` — a 1-based
line index within the diff hunk — not a file line number. `post_review.py` parses the
unified diff via `build_position_map()` and resolves every `(file, line_number) → position`
before submitting. All N inline comments are posted in a **single API call** regardless
of how many findings there are.

**Total API budget: 4 calls per review (constant)**

| Call # | What | Why |
|---|---|---|
| 1 | `gh pr view --json headRefOid` | Get HEAD commit SHA |
| 2 | `gh pr diff` | Build line→position map |
| 3 | `POST /pulls/{pr}/reviews` | All inline comments in one batch |
| 4 | `gh pr comment` | Top-level summary |

---

### Mode 3 — Standalone CLI

A Python package (`src/pr_reviewer/`) with a Typer CLI. Uses the Anthropic API directly
for an agentic multi-turn loop. Supports GitHub and Bitbucket.

**Package layout:**

```
src/pr_reviewer/
├── cli.py                    # Typer entrypoint: pr-reviewer review --url ...
├── config.py                 # YAML + env-var config loader
├── models.py                 # Pydantic v2 models for all data structures
├── agent/
│   ├── reviewer.py           # PRReviewer agentic loop
│   ├── tool_definitions.py   # Claude tool schemas
│   └── tool_executor.py      # Tool call dispatch
├── context/
│   ├── dependency_tracer.py  # Import/require graph tracing
│   ├── symbol_search.py      # Symbol-level cross-file search
│   ├── related_test_finder.py
│   ├── git_history.py
│   ├── repo_context_agent.py # One-shot context generation
│   └── repo_context_store.py # Save/load repo_context.json
├── output/
│   ├── formatter.py          # Finding → comment body rendering
│   ├── deduplicator.py       # Fingerprint + semantic dedup
│   └── poster.py             # CommentPoster (GitHub + Bitbucket)
└── platforms/
    ├── base.py               # Abstract PlatformAdapter interface
    ├── github_adapter.py     # PyGitHub + requests
    └── bitbucket_adapter.py  # Bitbucket Cloud REST API
```

---

### Mode 4 — EKS Event-Driven

Auto-reviews every PR without any manual trigger.

```
GitHub PR opened/updated
        │
        ▼ (webhook payload + HMAC validation)
FastAPI handler (deploy/webhook/app.py)
        │
        ▼ (kubernetes-client)
K8s Job spawned in pr-reviewer namespace
        │
        ▼
pr-reviewer review --url $PR_URL
        │
        ▼
Inline comments + summary posted to PR
```

**Infrastructure:** EKS cluster, ECR images, K8s RBAC (SA `webhook-handler` with
`jobs: create/get/list` permission), HMAC-validated webhook endpoint (ALB Ingress).

---

## Feedback Loop — Suppressions

The suppressions system prevents PRGenie from repeatedly flagging findings the team has
consciously accepted or dismissed.

```
/pr-review → findings posted → team responds
                                    │
              ┌─────────────────────┼──────────────────┐
              ▼                     ▼                   ▼
        Resolve thread        Reply "NAI"          (no 👍 — means
        (GitHub UI)           "false positive"      "good catch")
                              "won't fix" etc.
              └─────────────────────┼──────────────────┘
                                    ▼
                          /pr-feedback → suppressions.json
                                    │
                          Next /pr-review reads suppressions
                          Matched findings excluded from inline comments
                          Appear in 🔕 Suppressed Findings summary table
```

**Safety invariants (hard-coded, cannot be overridden):**
- `CRITICAL` severity findings are **never** suppressed
- `SECURITY` category findings are **never** suppressed

**suppressions.json schema:**
```json
{
  "version": "1.0",
  "suppressions": [
    {
      "id": "sup-001",
      "pattern": "raw JSON.parse without Zod validation",
      "category": "MAINTAINABILITY",
      "scope": "packages/opencode/src/util/",
      "reason": "Zod migration in progress, tracked in #456",
      "added_by": "psinghjpm",
      "added_at": "2026-02-28",
      "expires_at": null,
      "source_pr": 2
    }
  ]
}
```

The file can be committed to the repo (team-shared) or kept in
`~/.pr-reviewer/contexts/<owner>/<repo>/` (per-user global).

---

## External Context Providers

A file-based drop protocol allows external tools (Snyk, SonarQube, JIRA) to feed
their findings into PRGenie without any changes to the skill or CLI.

```
CI pipeline runs before /pr-review:

snyk code test --json | python .pr-reviewer/adapters/snyk.py
                                    │
                                    ▼
                    .pr-reviewer/context/snyk.json

python .pr-reviewer/adapters/sonar.py --url ... --project ...
                                    │
                                    ▼
                    .pr-reviewer/context/sonar.json

                   /pr-review reads *.json at Phase 0
                   External findings seeded into candidate list
                   Attributed as: source: snyk / source: sonar
                   Merged with Claude findings (no duplicates)
```

**Adding a new provider:** write a script that outputs the context schema JSON into
`.pr-reviewer/context/`. Zero changes to PRGenie itself.

---

## Repo Context Onboarding

A one-time `/repo-onboard` run generates `repo_context.json` — a structured knowledge
file that makes every subsequent review convention-aware.

**What it captures:**
- Language + framework stack
- Naming conventions (camelCase, PascalCase, kebab-case per layer)
- Architecture pattern (namespace modules, layered services, etc.)
- Security-sensitive paths (any PR touching these gets flagged at minimum MEDIUM)
- Review hints (known pitfalls: "no semicolons", "use Bun's $ not spawn", etc.)
- Test framework and co-location conventions
- Error handling patterns

Once generated, `/pr-review` applies this context across all five passes automatically.
