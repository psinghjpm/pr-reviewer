# pr-reviewer — Claude Code Context

## Project Overview

Agentic code review tool powered by Claude. Three modes:

| Mode | What it does | API key? |
|---|---|---|
| **MCP local reviewer** | Reviews staged changes before commit (in IDE) | No |
| **`/pr-review` skill** | Reviews a PR inside Claude Code | No (uses Claude Pro) |
| **Standalone CLI** | Reviews a PR via Anthropic API | Yes |

## Repository Layout

```
pr-reviewer/
├── mcp_server/
│   ├── server.py         # FastMCP server — list_staged_files, get_recent_commits, get_staged_changes
│   └── README.md         # MCP setup guide (Claude Code, Cline, Roo Code, Copilot)
├── skill/
│   ├── SKILL.md          # Claude Code /pr-review skill prompt
│   └── post_review.py    # Generic GitHub PR comment poster (called by the skill)
├── src/pr_reviewer/      # Python package (standalone CLI mode)
│   ├── cli.py            # Typer CLI entrypoint
│   ├── config.py         # YAML + env-var config (api_key optional)
│   ├── models.py         # All Pydantic v2 models
│   ├── agent/            # PRReviewer agentic loop, tool definitions, tool executor
│   ├── context/          # DependencyTracer, SymbolSearch, RelatedTestFinder, GitHistory
│   ├── output/           # Formatter, Deduplicator, CommentPoster
│   └── platforms/        # Abstract PlatformAdapter + GitHubAdapter + BitbucketAdapter
├── deploy/
│   ├── webhook/
│   │   ├── app.py        # FastAPI webhook handler — receives GitHub events, spawns K8s Jobs
│   │   └── Dockerfile    # Webhook image (fastapi + uvicorn + kubernetes client)
│   ├── k8s/              # Kubernetes manifests (namespace, secrets, rbac, deployment, job)
│   └── README.md         # EKS deployment walkthrough
├── .roo/modes/
│   └── code-reviewer.json  # Cline/Roo Code custom mode with review persona
├── .github/agents/
│   └── code-reviewer.agent.md  # GitHub Copilot custom agent config
├── tests/
│   ├── unit/             # Pure unit tests (no network)
│   ├── integration/      # Mock-adapter tests (no network)
│   └── e2e/              # Live network tests (require RUN_E2E=1)
├── config.example.yaml   # All config options with documentation
├── pyproject.toml        # Package metadata + dependencies (mcp optional extra)
└── CLAUDE.md             # This file
```

## MCP Local Pre-Commit Reviewer

Exposes three MCP tools that let any MCP-compatible IDE review staged changes
before commit. No API key needed — the IDE's own LLM does the analysis.

### Tools

| Tool | Args | Purpose |
|---|---|---|
| `list_staged_files` | `workspace_path?` | Quick file-level overview of staged changes |
| `get_recent_commits` | `workspace_path?`, `count?` (1–50) | Recent commit history for intent context |
| `get_staged_changes` | `workspace_path?`, `include_unstaged?` | Full diff + file contents for review |

### Register with Claude Code

```bash
pip install -e ".[mcp]"
claude mcp add pr-reviewer -- pr-reviewer-mcp
```

Then in any Claude Code session: `review my staged changes`

### Implementation notes

- `mcp_server/server.py` uses `FastMCP` (stdio transport)
- `_run()` checks `returncode` — returns `""` on git failure so callers degrade gracefully
- `count` in `get_recent_commits` is clamped: `max(1, min(count, 50))`
- `type_label` in `list_staged_files` covers A/M/D/R/C git status codes
- Entry point: `pr-reviewer-mcp = "mcp_server.server:mcp.run"` in `pyproject.toml`

## Skill Installation (Claude Code /pr-review)

```bash
mkdir -p ~/.claude/skills/pr-review
cp skill/SKILL.md ~/.claude/skills/pr-review/SKILL.md
cp skill/post_review.py ~/.claude/skills/pr-review/post_review.py
```

Inside Claude Code:
```
/pr-review https://github.com/owner/repo/pull/123
/pr-review https://github.com/owner/repo/pull/123 --dry-run
/pr-review https://github.com/owner/repo/pull/123 --min-severity HIGH
```

### How the skill works end-to-end
1. Claude fetches PR metadata + diff via `gh pr view` / `gh pr diff`
2. Claude fetches full file contents, related test files, existing comments (for dedup)
3. Claude runs a 5-pass analysis: INTENT → LOGIC/BUGS → SECURITY → QUALITY → TESTS
4. Claude writes `/tmp/pr_comments.json` and `/tmp/pr_summary.md`
5. Claude calls `skill/post_review.py` to post summary first, then inline comments

### post_review.py posting order
Summary is posted **before** inline comments so it appears at the top of the
PR timeline. (`post_summary()` is called before `post_inline_review()`.)

## Standalone CLI (API mode)

```bash
pip install -e .

# GitHub PR
ANTHROPIC_API_KEY=... GITHUB_TOKEN=... \
  pr-reviewer review --url https://github.com/owner/repo/pull/123

# Dry run
pr-reviewer review --url ... --dry-run

# Bitbucket PR
ANTHROPIC_API_KEY=... BITBUCKET_USERNAME=... BITBUCKET_APP_PASSWORD=... \
  pr-reviewer review --platform bitbucket --workspace ws --repo repo --pr 1
```

## EKS Event-Driven Deployment

`deploy/webhook/app.py` — FastAPI handler that receives GitHub PR webhooks,
validates the HMAC signature, and spawns a K8s Job that runs
`pr-reviewer review --url $PR_URL`.

Key env vars for the webhook pod:
- `WEBHOOK_SECRET` — GitHub webhook HMAC secret
- `REVIEWER_IMAGE` — Docker image for the reviewer Job
- `K8S_NAMESPACE` — namespace to create Jobs in (default: `pr-reviewer`)
- `K8S_JOB_SECRET_NAME` — Secret holding `ANTHROPIC_API_KEY` + `GITHUB_TOKEN`

Apply manifests in order: namespace → secrets → rbac → webhook-deploy.
See `deploy/README.md` for the full walkthrough.

## Running Tests

```bash
pytest tests/unit/ -v                        # fast, no network
pytest tests/integration/ -v                 # mock adapter, no network
pytest tests/unit/ tests/integration/ -v     # all non-E2E
RUN_E2E=1 E2E_GITHUB_REPO=owner/repo E2E_GITHUB_PR=1 pytest tests/e2e/ -v
```

## Key Decisions & Conventions

- **Pydantic v2**: `model_config = {"arbitrary_types_allowed": True}`
- **Windows paths**: `.as_posix()` for repo-relative paths in API calls
- **gh CLI on Windows**: `C:\Program Files\GitHub CLI\gh.exe` — pass via `--gh-path`
- **Windows console encoding**: `post_review.py` calls `sys.stdout.reconfigure(encoding="utf-8")`
- **Backend auto-detection**: CLI checks for `ANTHROPIC_API_KEY`; if absent, prints `/pr-review` hint
- **Severity ordering**: CRITICAL > HIGH > MEDIUM > LOW > INFO
- **Comment deduplication**: existing PR comments fetched before posting; duplicates skipped
- **MCP transport**: stdio (IDEs launch `pr-reviewer-mcp` as a subprocess)

## Environment Variables

| Variable | Required | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | API mode + EKS | Standalone CLI, K8s Job |
| `GITHUB_TOKEN` | GitHub PRs | All PR review modes |
| `BITBUCKET_USERNAME` | Bitbucket PRs | Standalone CLI |
| `BITBUCKET_APP_PASSWORD` | Bitbucket PRs | Standalone CLI |
| `WEBHOOK_SECRET` | EKS webhook | FastAPI handler |
| `REVIEWER_IMAGE` | EKS webhook | FastAPI handler |
