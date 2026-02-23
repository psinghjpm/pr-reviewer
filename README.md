# pr-reviewer

Agentic Pull Request review tool powered by Claude — rivals CodeRabbit, Qodo, Greptile, and Graphite.

Works in **two modes** depending on what you have:

| Mode | Requirement | Cost |
|---|---|---|
| **Claude Code** (recommended) | Claude Pro/Max subscription | Free — uses your existing session |
| **Standalone API** | `ANTHROPIC_API_KEY` | ~$0.50–$2 per review (pay-as-you-go) |

---

## Mode 1 — Claude Code (Free with Claude Pro)

The `/pr-review` slash command runs the full review inside your Claude Code session.
No API key needed — it uses your Claude Pro subscription.

```bash
# Inside Claude Code, just type:
/pr-review https://github.com/owner/repo/pull/123

# Dry run (analyze but don't post comments):
/pr-review https://github.com/owner/repo/pull/123 --dry-run

# Only post HIGH and above:
/pr-review https://github.com/owner/repo/pull/123 --min-severity HIGH
```

The slash command is installed at `~/.claude/commands/pr-review.md`.
It supports both GitHub and Bitbucket.

**Requirements:**
- `gh` CLI authenticated (`gh auth login`) for GitHub PRs
- `BITBUCKET_USERNAME` + `BITBUCKET_APP_PASSWORD` env vars for Bitbucket PRs

---

## Mode 2 — Standalone CLI (API key)

```bash
pip install pr-reviewer

# GitHub PR (auto-detects platform from URL)
GITHUB_TOKEN=... ANTHROPIC_API_KEY=... \
  pr-reviewer review --url https://github.com/owner/repo/pull/123

# Bitbucket PR
BITBUCKET_USERNAME=... BITBUCKET_APP_PASSWORD=... ANTHROPIC_API_KEY=... \
  pr-reviewer review --platform bitbucket --workspace ws --repo repo --pr 1

# Dry run (analyze, print findings, don't post)
pr-reviewer review --url https://github.com/owner/repo/pull/123 --dry-run

# Limit cost with fewer tool calls
pr-reviewer review --url ... --max-tool-calls 20

# Force claudecode backend (prints /pr-review hint instead of running)
pr-reviewer review --url ... --backend claudecode
```

**Auto-detection:** if `ANTHROPIC_API_KEY` is set → uses API backend automatically.
If not set → prints the Claude Code slash command hint.

---

## Configuration

```bash
pr-reviewer config init   # creates config.yaml
```

See `config.example.yaml` for all options. Environment variables always override config file.

---

## CI/CD

### GitHub Actions
Add `.github/workflows/pr-review.yml` (included). Requires:
- `ANTHROPIC_API_KEY` secret (uses API backend in CI)
- `GITHUB_TOKEN` (auto-provided)

### Bitbucket Pipelines
Add `bitbucket-pipelines.yml` (included). Requires:
- `ANTHROPIC_API_KEY`, `BITBUCKET_USERNAME`, `BITBUCKET_APP_PASSWORD` secrets

---

## Running Tests

```bash
# Unit tests (no network)
pytest tests/unit/ -v

# Integration tests (mock adapter, no network)
pytest tests/integration/ -v

# E2E (live network, posts real comments)
RUN_E2E=1 E2E_GITHUB_REPO=owner/repo E2E_GITHUB_PR=1 pytest tests/e2e/ -v
```

---

## Features

- **Multi-platform**: GitHub and Bitbucket Cloud
- **Two backends**: Claude Code (free with Pro) + standalone API
- **Context-aware**: tree-sitter AST dependency tracing, symbol search, test file discovery
- **Agentic**: 5-pass review (Intent → Context → Logic/Bugs → Security → Tests)
- **Smart dedup**: fingerprint + semantic deduplication of existing comments
- **CI/CD ready**: GitHub Actions and Bitbucket Pipelines included
