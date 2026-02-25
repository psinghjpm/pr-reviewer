# pr-reviewer MCP Server — Local Pre-Commit Reviewer

Reviews your **staged git changes** inside your IDE before you commit.
No PR, no push, no API key required.

```
git add -p          ← stage your changes
"review my code"    ← ask Cline / Copilot
     ↓
MCP tool reads: git diff --cached + full file contents
     ↓
IDE's LLM analyses the diff (bugs, security, quality, tests)
     ↓
Findings appear in the chat window
     ↓
git commit          ← fix first, then commit
```

---

## Install

```bash
pip install -e ".[mcp]"
```

This installs the `pr-reviewer-mcp` command (FastMCP server, stdio transport).

---

## Cline (VSCode)

### 1 — Register the MCP server

Open the Cline MCP settings (`~/.cline/mcp_settings.json` or via the Cline UI → MCP → Add Server):

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

If `pr-reviewer-mcp` is not on your PATH, use the full path:
```json
"command": "/path/to/venv/bin/pr-reviewer-mcp"
```

### 2 — Enable the Code Reviewer mode

The `.roo/modes/code-reviewer.json` file in this repo defines a custom Cline/Roo mode.
Roo Code picks it up automatically when you open this workspace.

Switch to **Code Reviewer** mode in the Cline/Roo mode selector.

### 3 — Use it

Stage your changes, then in Cline chat:
```
Review my staged changes
```

---

## Roo Code (VSCode)

Same as Cline above. Roo Code reads `.roo/modes/` automatically — the
**Code Reviewer** mode will appear in your mode selector when you open
this workspace.

---

## GitHub Copilot (VSCode 1.99+)

### 1 — Register the MCP server

Open VSCode settings (`Ctrl+,`) → search `mcp` → **Edit in settings.json**:

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

### 2 — The Custom Agent is already configured

`.github/agents/code-reviewer.agent.md` in this repo registers a
**Code Reviewer** agent in Copilot Chat automatically.

### 3 — Use it

In Copilot Chat, switch to Agent mode and select **Code Reviewer**, or:
```
@code-reviewer review my staged changes
```

---

## Passing workspace path

If your IDE launches the MCP server from a different working directory,
pass the path explicitly in the tool call:

```
review staged changes in /Users/me/projects/my-app
```

The `get_staged_changes` tool accepts an optional `workspace_path` argument.

---

## What the tool returns

```
## Git workspace context
Branch: feature/auth
Recent commits: ...
Staged summary: 3 files changed, 120 insertions

## Unified diff
...full diff...

## Full file contents (changed files)
### FILE: src/auth/login.py
...complete file content...
```

The IDE's LLM receives this and produces a structured review (INTENT →
LOGIC/BUGS → SECURITY → QUALITY → TESTS) with file + line references,
concrete fixes, and a go/no-go recommendation.
