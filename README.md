# pr-reviewer

Agentic code review tool powered by Claude — catches bugs, security issues, and quality problems at every stage of the development lifecycle.

## The full review lifecycle

```
git add          →  [MCP: local pre-commit review]  →  git commit  →  git push  →  PR opened  →  [pr-reviewer]
                     IDE chat, no API key needed                                      GitHub/Bitbucket comments
                     catches it before push                                           gatekeeps before merge
```

Two complementary tools — one for **before you commit**, one for **after you push**.

---

## Tool 1 — Local Pre-Commit Review (MCP, free)

Reviews your **staged git changes** right inside your IDE before you commit.
No PR, no push, no API key needed — the IDE's own LLM (Claude in Claude Code,
GPT-4o/Claude in Copilot) does the analysis.

### Quick start (Claude Code)

```bash
# 1. Install
pip install -e ".[mcp]"

# 2. Register with Claude Code (once per machine)
claude mcp add pr-reviewer -- pr-reviewer-mcp

# 3. Stage your changes and ask for a review
git add src/my_feature.py
# in Claude Code: "review my staged changes"
```

### What the MCP server exposes

| Tool | Purpose |
|---|---|
| `list_staged_files` | Quick overview — which files are staged and how (added/modified/deleted) |
| `get_recent_commits` | Recent commit history for understanding intent (up to 50 commits) |
| `get_staged_changes` | Full diff + complete file contents for all changed files |

The IDE LLM runs a structured 5-pass review on the returned context:
**INTENT → LOGIC/BUGS → SECURITY → QUALITY → TESTS**

Each finding includes: file path + line number, the problem, and a concrete fix.
A go/no-go recommendation ends the review.

### IDE setup

See **[mcp_server/README.md](mcp_server/README.md)** for step-by-step setup for:
- Claude Code (terminal)
- Cline (VSCode)
- Roo Code (VSCode)
- GitHub Copilot (VSCode 1.99+)

---

## Tool 2 — PR Review (post-push, GitHub + Bitbucket)

Reviews an open pull request and posts inline comments + a summary directly
to GitHub or Bitbucket. Three ways to run it:

### Option A — Claude Code skill (free with Claude Pro)

```bash
# Install the skill (once)
mkdir -p ~/.claude/skills/pr-review
cp skill/SKILL.md ~/.claude/skills/pr-review/SKILL.md
cp skill/post_review.py ~/.claude/skills/pr-review/post_review.py

# Inside Claude Code:
/pr-review https://github.com/owner/repo/pull/123
/pr-review https://github.com/owner/repo/pull/123 --dry-run
/pr-review https://github.com/owner/repo/pull/123 --min-severity HIGH
```

No API key needed — uses your Claude Pro/Max subscription.
Requires `gh` CLI authenticated (`gh auth login`).

### Option B — Standalone CLI (Anthropic API key)

```bash
pip install pr-reviewer

# GitHub PR
GITHUB_TOKEN=... ANTHROPIC_API_KEY=... \
  pr-reviewer review --url https://github.com/owner/repo/pull/123

# Bitbucket PR
BITBUCKET_USERNAME=... BITBUCKET_APP_PASSWORD=... ANTHROPIC_API_KEY=... \
  pr-reviewer review --platform bitbucket --workspace ws --repo repo --pr 1

# Dry run — analyze but don't post
pr-reviewer review --url ... --dry-run

# Only show HIGH and above
pr-reviewer review --url ... --min-severity HIGH
```

### Option C — EKS event-driven (auto-review every PR)

Deploys a FastAPI webhook handler on Kubernetes that spawns a Job automatically
on every `pull_request` `opened`/`synchronize` GitHub event.

See **[deploy/README.md](deploy/README.md)** for the full EKS deployment guide.

```
GitHub PR opened → webhook → FastAPI handler → K8s Job → pr-reviewer review --url $PR_URL
```

---

## Modes at a glance

| | Local MCP | Claude Code skill | Standalone CLI | EKS |
|---|---|---|---|---|
| **When** | Before commit | After PR opens | After PR opens | After PR opens |
| **API key** | No | No (Claude Pro) | Yes | Yes |
| **Output** | IDE chat | PR comments | PR comments | PR comments |
| **Platforms** | Any git repo | GitHub + Bitbucket | GitHub + Bitbucket | GitHub |
| **Setup** | `pip install` + 1 command | Copy 2 files | `pip install` | Helm/kubectl |

---

## Configuration

```bash
pr-reviewer config init   # creates config.yaml
```

See `config.example.yaml` for all options. Environment variables always override the config file.

### Environment variables

| Variable | Required by |
|---|---|
| `ANTHROPIC_API_KEY` | Standalone CLI, EKS Job |
| `GITHUB_TOKEN` | GitHub PRs (all modes) |
| `BITBUCKET_USERNAME` | Bitbucket PRs |
| `BITBUCKET_APP_PASSWORD` | Bitbucket PRs |

---

## CI/CD

### GitHub Actions
Add `.github/workflows/pr-review.yml` (included). Requires `ANTHROPIC_API_KEY` secret.

### Bitbucket Pipelines
Add `bitbucket-pipelines.yml` (included). Requires `ANTHROPIC_API_KEY`, `BITBUCKET_USERNAME`, `BITBUCKET_APP_PASSWORD`.

---

## Running Tests

```bash
pytest tests/unit/ -v           # fast, no network
pytest tests/integration/ -v   # mock adapter, no network
RUN_E2E=1 E2E_GITHUB_REPO=owner/repo E2E_GITHUB_PR=1 pytest tests/e2e/ -v
```

---

## Features

- **Left-shift**: catches issues before commit via MCP local reviewer
- **Multi-platform**: GitHub and Bitbucket Cloud
- **Multiple backends**: Claude Code (free with Pro), standalone API, EKS auto-review
- **Context-aware**: tree-sitter AST tracing, symbol search, related test discovery
- **5-pass analysis**: INTENT → LOGIC/BUGS → SECURITY → QUALITY → TESTS
- **Smart dedup**: fingerprint + semantic deduplication skips already-posted comments
- **CI/CD ready**: GitHub Actions and Bitbucket Pipelines included
- **EKS native**: event-driven K8s Jobs, one review per PR event, zero manual triggers
