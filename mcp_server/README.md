# pr-reviewer MCP Server — Local Pre-Commit Reviewer

Reviews your **staged git changes** inside your IDE before you commit.
No PR, no push, no API key required.

```
git add -p               ← stage your changes
"review my staged changes"  ← ask your IDE
        ↓
MCP server reads: git diff --cached + file contents + recent history
        ↓
IDE's LLM analyses the diff (bugs, security, quality, tests)
        ↓
Findings appear in the chat window with file + line references
        ↓
git commit               ← fix first, then commit
```

---

## MCP Tools

The server exposes three tools. The IDE's LLM calls them automatically when you ask for a review.

### `list_staged_files(workspace_path?)`

Quick overview of what's staged before fetching the full diff.

```
Staged files:
  modified    src/auth/login.py
  added       tests/test_login.py
  deleted     src/auth/legacy.py
```

### `get_recent_commits(workspace_path?, count?)`

Returns recent commit history (default 10, max 50) so the LLM can understand
the intent behind the staged changes.

```
Recent 10 commits:

a1b2c3d  2026-02-24  Jane Dev  feat: add OAuth2 login
...
```

### `get_staged_changes(workspace_path?, include_unstaged?)`

The main review tool. Returns:
- Unified diff with 5 lines of context around each change
- Full current content of every changed file
- Branch name, recent commits, and staged summary

Set `include_unstaged=true` to also review working-tree changes not yet staged.

---

## Install

```bash
pip install -e ".[mcp]"
```

This installs the `pr-reviewer-mcp` command (FastMCP server, stdio transport).
The server runs as a local subprocess — no network access, no API key needed.

---

## Claude Code (recommended)

### 1 — Register the server

```bash
claude mcp add pr-reviewer -- pr-reviewer-mcp
```

If `pr-reviewer-mcp` is not on your PATH, use the full path:
```bash
claude mcp add pr-reviewer -- /full/path/to/pr-reviewer-mcp
```

On Windows (Python installed to AppData):
```bash
claude mcp add pr-reviewer -- C:/Users/<you>/AppData/Local/Programs/Python/Python313/Scripts/pr-reviewer-mcp
```

### 2 — Use it

Stage your changes, then in any Claude Code session:
```
review my staged changes
```

Claude Code calls `list_staged_files` → `get_staged_changes` and runs the
5-pass review (INTENT → LOGIC/BUGS → SECURITY → QUALITY → TESTS) in the
terminal. No extra setup or API key needed.

---

## Cline (VSCode)

### 1 — Register the MCP server

Open the Cline MCP settings via the Cline UI → MCP → Add Server, or edit
`~/.cline/mcp_settings.json` directly:

```json
{
  "mcpServers": {
    "pr-reviewer": {
      "command": "pr-reviewer-mcp",
      "args": [],
      "env": {}
    }
  }
}
```

### 2 — Enable the Code Reviewer mode

`.roo/modes/code-reviewer.json` in this repo defines a Cline custom mode
with the review persona pre-loaded. Cline picks it up automatically when you
open this workspace.

Switch to **Code Reviewer** mode in the Cline mode selector.

### 3 — Use it

```
Review my staged changes
```

---

## Roo Code (VSCode)

Same steps as Cline. Roo Code reads `.roo/modes/` automatically — the
**Code Reviewer** mode appears in the mode selector when this workspace is open.

---

## GitHub Copilot (VSCode 1.99+)

### 1 — Register the MCP server

`Ctrl+,` → search `mcp` → **Edit in settings.json**:

```json
{
  "mcp": {
    "servers": {
      "pr-reviewer": {
        "type": "stdio",
        "command": "pr-reviewer-mcp",
        "args": []
      }
    }
  }
}
```

### 2 — Use it

`.github/agents/code-reviewer.agent.md` in this repo registers a
**Code Reviewer** custom agent automatically. In Copilot Chat:

```
@code-reviewer review my staged changes
```

---

## Specifying a workspace path

If the IDE launches the MCP server from a different working directory, pass
the repo path explicitly:

```
review staged changes in /Users/me/projects/my-app
```

All three tools accept an optional `workspace_path` argument.

---

## Review methodology

The IDE LLM receives the diff + file contents and performs a structured review:

| Pass | What it checks |
|---|---|
| **INTENT** | What is this change trying to do? (uses commits + context) |
| **LOGIC & BUGS** | Wrong conditions, null handling, off-by-one, race conditions |
| **SECURITY** | Injection, hardcoded secrets, missing auth, OWASP Top 10 |
| **QUALITY** | Complexity, duplication, missing error handling at system boundaries |
| **TESTS** | Are changed paths covered? Are edge cases tested? |

Findings are grouped by severity: **CRITICAL → HIGH → MEDIUM → LOW**,
each with file path + line number, the problem, and a concrete fix.
Ends with a clear **go / fix-first** recommendation.
