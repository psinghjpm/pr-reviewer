#!/usr/bin/env python3
"""
pr-reviewer MCP server — local staged-change context provider.

Exposes three tools:
    list_staged_files(workspace_path?)
    get_recent_commits(workspace_path?, count?)
    get_staged_changes(workspace_path?, include_unstaged?)

The tool reads the local git workspace and returns:
  - The unified diff (staged, or staged+unstaged)
  - Full current content of every changed file
  - Basic git metadata (branch, recent commits)

The IDE's LLM (Claude in Cline/Roo, GPT-4o/Claude in Copilot) receives
this context and performs the code review inside the chat conversation.
No Anthropic API key is needed here.

Install:
    pip install -e ".[mcp]"

Run (stdio transport — IDEs launch this as a subprocess):
    pr-reviewer-mcp

Configure in your IDE — see mcp_server/README.md.
"""

import subprocess
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP(
    name="pr-reviewer",
    instructions=(
        "Provides local git workspace context (staged diff + file contents) "
        "so you can review code changes before they are committed. "
        "Call get_staged_changes, then analyse the returned diff and files "
        "for bugs, security issues, logic errors, test coverage, and code quality. "
        "Use list_staged_files for a quick overview before fetching the full diff."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(cmd: list[str], cwd: str) -> str:
    """Run a git command and return stdout. Returns empty string on error."""
    result = subprocess.run(
        cmd, cwd=cwd, text=True, capture_output=True, encoding="utf-8", errors="replace"
    )
    return result.stdout.strip()


def _changed_files(diff_output: str) -> list[str]:
    """Extract the set of changed file paths from a unified diff."""
    files: list[str] = []
    for line in diff_output.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            if path not in files:
                files.append(path)
    return files


def _read_file(workspace: str, rel_path: str) -> str:
    """Read a file relative to the workspace root."""
    full = Path(workspace) / rel_path
    if not full.exists():
        return f"[file not found: {rel_path}]"
    try:
        return full.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return f"[could not read {rel_path}: {exc}]"


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------

@mcp.tool()
def list_staged_files(workspace_path: str = ".") -> str:
    """
    Return a compact list of files that are currently staged (git add-ed)
    along with their change type (added/modified/deleted/renamed).
    Use this for a quick overview before calling get_staged_changes.

    Args:
        workspace_path: Absolute or relative path to the git repository root.
    """
    ws = str(Path(workspace_path).resolve())
    output = _run(["git", "diff", "--cached", "--name-status"], cwd=ws)
    if not output:
        return "No staged files. Run `git add` to stage changes."

    lines = []
    type_label = {"A": "added", "M": "modified", "D": "deleted", "R": "renamed", "C": "copied"}
    for line in output.splitlines():
        parts = line.split("\t", 2)
        change = type_label.get(parts[0][0], parts[0])
        path = parts[-1]
        lines.append(f"  {change:10s}  {path}")

    return "Staged files:\n" + "\n".join(lines)


@mcp.tool()
def get_recent_commits(workspace_path: str = ".", count: int = 10) -> str:
    """
    Return the most recent git commits with author, date and message.
    Useful for understanding the intent behind staged changes.

    Args:
        workspace_path: Absolute or relative path to the git repository root.
        count:          Number of commits to return (default 10, max 50).
    """
    ws = str(Path(workspace_path).resolve())
    n = max(1, min(count, 50))
    log = _run(
        ["git", "log", f"-{n}", "--pretty=format:%h  %ad  %an  %s", "--date=short"],
        cwd=ws,
    )
    if not log:
        return "No commits found."
    return f"Recent {n} commits:\n\n" + log


@mcp.tool()
def get_staged_changes(
    workspace_path: str = ".",
    include_unstaged: bool = False,
) -> str:
    """
    Return the current staged (and optionally unstaged) git changes plus the
    full content of every modified file. Use this as context to review code
    before it is committed.

    Args:
        workspace_path:   Absolute or relative path to the git repository root.
                          Defaults to the current working directory.
        include_unstaged: Also include unstaged working-tree changes
                          (git diff in addition to git diff --cached).
    """
    ws = str(Path(workspace_path).resolve())

    # --- diff ---------------------------------------------------------------
    staged_diff = _run(["git", "diff", "--cached", "--unified=5"], cwd=ws)
    unstaged_diff = _run(["git", "diff", "--unified=5"], cwd=ws) if include_unstaged else ""

    if not staged_diff and not unstaged_diff:
        return (
            "No staged changes found.\n"
            "Stage files first with `git add` then ask for a review."
        )

    full_diff = staged_diff
    if unstaged_diff:
        full_diff += "\n\n--- UNSTAGED CHANGES ---\n\n" + unstaged_diff

    # --- metadata -----------------------------------------------------------
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=ws)
    recent_log = _run(["git", "log", "--oneline", "-5"], cwd=ws)
    staged_stat = _run(["git", "diff", "--cached", "--stat"], cwd=ws)

    # --- file contents ------------------------------------------------------
    changed = _changed_files(full_diff)
    file_sections: list[str] = []
    for rel_path in changed:
        content = _read_file(ws, rel_path)
        file_sections.append(
            f"### FILE: {rel_path}\n```\n{content}\n```"
        )

    files_block = "\n\n".join(file_sections) if file_sections else "(no files read)"

    # --- assemble -----------------------------------------------------------
    return f"""## Git workspace context

**Branch:** {branch}
**Recent commits:**
{recent_log}

**Staged changes summary:**
{staged_stat}

---

## Unified diff
```diff
{full_diff}
```

---

## Full file contents (changed files)

{files_block}
"""
