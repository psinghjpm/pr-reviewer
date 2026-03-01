# PRGenie — Claude Code Context

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
│   ├── SKILL.md          # Claude Code /pr-review skill prompt (Phase 0 loads repo context)
│   ├── ONBOARD_SKILL.md  # Claude Code /repo-onboard skill prompt
│   ├── FEEDBACK_SKILL.md # Claude Code /pr-feedback skill prompt (harvests dismissals → suppressions.json)
│   └── post_review.py    # Generic GitHub PR comment poster (called by the skill)
├── src/pr_reviewer/      # Python package (standalone CLI mode)
│   ├── cli.py            # Typer CLI entrypoint
│   ├── config.py         # YAML + env-var config (api_key optional)
│   ├── models.py         # All Pydantic v2 models
│   ├── agent/            # PRReviewer agentic loop, tool definitions, tool executor
│   ├── context/          # DependencyTracer, SymbolSearch, RelatedTestFinder, GitHistory,
│   │                     #   RepoContextAgent, RepoContextStore
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
├── .pr-reviewer/
│   ├── suppressions.json          # Team-shared suppression rules
│   ├── context/                   # Drop-zone for external provider JSON files
│   └── adapters/
│       ├── snyk.py                # Snyk Code → context provider schema
│       ├── sonar.py               # SonarQube/SonarCloud → context provider schema
│       └── jira.py                # JIRA ticket validation → context provider schema
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

# Install pr-feedback skill
mkdir -p ~/.claude/skills/pr-feedback
cp skill/FEEDBACK_SKILL.md ~/.claude/skills/pr-feedback/SKILL.md
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
5. Claude calls `skill/post_review.py` to post summary first, then all inline comments in one batch

### post_review.py posting order and API budget
Summary is posted **before** inline comments so it appears at the top of the
PR timeline. (`post_summary()` is called before `post_inline_review()`.)

Posting uses exactly **4 API calls** regardless of how many findings there are:

| Call | What | Why |
|---|---|---|
| 1 | `gh pr view --json headRefOid` | Fetch HEAD commit SHA |
| 2 | `gh pr diff` | Fetch unified diff to build line→position map |
| 3 | `POST /repos/{owner}/{repo}/pulls/{pr}/reviews` | All N inline comments in one batch |
| 4 | `gh pr comment` | Top-level summary comment |

**Why `position` not `line`:** GitHub's batch review API (`POST /pulls/{pr}/reviews`) requires
each comment to specify `position` — the 1-based line index within the file's diff hunk — not
the `line` + `side` fields used by the single-comment endpoint. `post_review.py` parses the
unified diff via `build_position_map()` to resolve every `(file, line_number)` → `position`
before submitting the batch. Comments that fall outside the diff are skipped with a warning
rather than aborting the review.

**Value Metrics:** `post_review.py` also appends a **📊 Review Value Metrics** section to the
summary (defect prevention estimate based on IBM/NIST benchmarks + reviewer time saved).
Disable with `--no-metrics`; tune cost assumptions with `--cost-critical`, `--cost-high`,
`--cost-medium`, `--cost-low`, `--hourly-rate` (default: 10000 / 2500 / 500 / 100 / $200/hr).

## Repo Context Onboarding

A one-time onboarding step generates a persistent `RepoContext` JSON file that makes
every subsequent review convention-aware (naming, architecture, security paths, review hints).

### Storage locations (local takes precedence)

| Scope | Path | Notes |
|---|---|---|
| Local | `.pr-reviewer/repo_context.json` | Committable; team-shareable |
| Global | `~/.pr-reviewer/contexts/<owner>/<repo>/repo_context.json` | Per-user default |

### CLI onboard command

```bash
# Generate and save to global path
pr-reviewer onboard https://github.com/owner/repo

# Save to a custom path (e.g. local scope for the whole team)
pr-reviewer onboard https://github.com/owner/repo --output .pr-reviewer/repo_context.json

# Overwrite existing context after significant repo changes
pr-reviewer onboard https://github.com/owner/repo --force
```

Requires: `ANTHROPIC_API_KEY` + `GITHUB_TOKEN`

### Skill onboard (Claude Code)

```bash
cp skill/ONBOARD_SKILL.md ~/.claude/skills/repo-onboard/ONBOARD_SKILL.md
```

Inside Claude Code:
```
/repo-onboard
/repo-onboard --force
/repo-onboard --output ./service-a/.pr-reviewer/repo_context.json
```

### Auto-load in reviews

Once generated, repo context is loaded automatically:
- **CLI:** `pr-reviewer review --url ...` checks `.pr-reviewer/repo_context.json` (CWD),
  then `~/.pr-reviewer/contexts/<owner>/<repo>/repo_context.json`.
- **Skill:** `/pr-review` Phase 0 tries the same two paths.

### Implementation notes

- `src/pr_reviewer/models.py` — `RepoContext` Pydantic v2 model
- `src/pr_reviewer/context/repo_context_store.py` — `RepoContextStore` (save/load/find)
- `src/pr_reviewer/context/repo_context_agent.py` — `RepoContextAgent` (single-shot Claude call)
- `src/pr_reviewer/agent/reviewer.py` — `build_system_prompt(repo_context)` prepends context
- `RepoContextAgent.generate()` never raises — falls back to a minimal `RepoContext` on error
- `_parse_response()` strips markdown fences before parsing JSON

## External Context Providers

External tools (Snyk, SonarQube, JIRA) plug in via a **file-based drop protocol**:
each adapter writes a JSON file to `.pr-reviewer/context/` before the review runs.
`/pr-review` Phase 0 globs that directory and merges all provider findings into the
review context automatically — no skill changes needed to add new providers.

### Directory layout

```
.pr-reviewer/
├── context/             ← drop-zone (gitignored in CI, committable for local debug)
│   ├── snyk.json        ← written by adapters/snyk.py
│   ├── sonar.json       ← written by adapters/sonar.py
│   └── jira.json        ← written by adapters/jira.py
└── adapters/
    ├── snyk.py          ← Snyk Code SARIF → context schema
    ├── sonar.py         ← SonarQube REST API → context schema
    └── jira.py          ← JIRA ticket validation → context schema
```

### Context provider schema (all adapters must conform)

```json
{
  "source": "<provider>",
  "version": "1.0",
  "generated_at": "<ISO timestamp>",
  "findings": [
    {
      "file": "packages/foo/bar.ts",
      "line": 42,
      "severity": "HIGH",
      "category": "SECURITY",
      "message": "Description of issue",
      "rule_id": "SNYK-JS-001",
      "url": "https://..."
    }
  ],
  "summary": {
    "passed": true,
    "label": "OK",
    "critical": 0, "high": 1, "medium": 3, "low": 2
  }
}
```

`findings` entries follow the same severity/category vocabulary as PRGenie findings.
`file` paths must be repo-relative. `url` and `rule_id` are optional but recommended.

### Adapter usage

```bash
# Snyk — pipe snyk CLI output directly
snyk code test --json | python .pr-reviewer/adapters/snyk.py

# Snyk — read from a saved file (CI pattern)
python .pr-reviewer/adapters/snyk.py --input /tmp/snyk.json

# SonarQube / SonarCloud
SONAR_TOKEN=... python .pr-reviewer/adapters/sonar.py \
  --url https://sonarcloud.io --project my-org_my-repo

# JIRA ticket validation
JIRA_TOKEN=... JIRA_USER=me@example.com \
  python .pr-reviewer/adapters/jira.py \
  --pr-url https://github.com/owner/repo/pull/123 \
  --jira-url https://myorg.atlassian.net
```

### CI integration pattern (GitHub Actions)

```yaml
- name: Run Snyk
  run: snyk code test --json | python .pr-reviewer/adapters/snyk.py
  env:
    SNYK_TOKEN: ${{ secrets.SNYK_TOKEN }}

- name: Run SonarQube
  run: |
    python .pr-reviewer/adapters/sonar.py \
      --url ${{ vars.SONAR_URL }} --project ${{ vars.SONAR_PROJECT }}
  env:
    SONAR_TOKEN: ${{ secrets.SONAR_TOKEN }}

- name: PR Review (picks up context automatically)
  run: /pr-review ${{ github.event.pull_request.html_url }}
```

### Adding a new provider

1. Write a script that fetches your tool's data and outputs the context provider schema JSON
2. Drop the output into `.pr-reviewer/context/<your-tool>.json`
3. Run `/pr-review` — findings appear in the review with `source: <your-tool>` attribution

No changes to `SKILL.md` or any other file needed.

### Environment variables for adapters

| Variable | Adapter | Required |
|---|---|---|
| `SNYK_TOKEN` | Snyk CLI (handled by CLI, not the adapter) | For snyk CLI auth |
| `SONAR_TOKEN` | sonar.py | Yes |
| `SONAR_URL` | sonar.py | Yes (or `--url`) |
| `SONAR_PROJECT` | sonar.py | Yes (or `--project`) |
| `JIRA_TOKEN` | jira.py | Yes |
| `JIRA_USER` | jira.py | Yes (Atlassian account email) |
| `JIRA_URL` | jira.py | Yes (or `--jira-url`) |

## Feedback Loop — Suppressions (Phase 1 + 2)

Prevent pr-reviewer from repeatedly flagging findings that your team has consciously dismissed.
Two components work together: a `suppressions.json` file (Phase 1) and a `/pr-feedback` skill
that auto-populates it from PR history (Phase 2).

### suppressions.json schema

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

**Fields:**
- `pattern` — plain-English phrase; Claude matches it semantically against each finding message
- `category` — optional exact match (`BUG`/`LOGIC`/`SECURITY`/etc.); omit to match any category
- `scope` — file path prefix; omit to match repo-wide
- `expires_at` — ISO date after which suppression is ignored; `null` = never expires
- `source_pr` — PR number the dismissal came from (informational)

### Storage locations (same precedence rule as repo_context.json)

| Scope | Path | Notes |
|---|---|---|
| Local | `.pr-reviewer/suppressions.json` | Committable; team-shareable |
| Global | `~/.pr-reviewer/contexts/<owner>/<repo>/suppressions.json` | Per-user default |

### Safety invariants (hard rules — cannot be overridden)

- `severity: CRITICAL` → **NEVER suppressed**
- `category: SECURITY` → **NEVER suppressed**

### /pr-feedback usage

```bash
# Harvest dismissed threads from a PR and write suppressions.json
/pr-feedback https://github.com/owner/repo/pull/123

# Preview candidates without writing
/pr-feedback https://github.com/owner/repo/pull/123 --dry-run

# Write to a custom path (e.g. team-shared local file)
/pr-feedback https://github.com/owner/repo/pull/123 --output .pr-reviewer/suppressions.json
```

A thread is treated as dismissed if ANY of:
1. GitHub "Resolve conversation" was clicked (`isResolved: true`)
2. A non-bot reply contains NAI keywords: `NAI`, `not an issue`, `false positive`,
   `won't fix`, `by design`, `not applicable`, `ignore`, `intentional`, `accepted risk`
3. The pr-reviewer comment received a 👍 reaction from a non-bot user

### Recommended workflow

```
1. Run /pr-review → findings posted to PR
2. Team reviews; dismisses false-positives (resolve, reply "NAI", or 👍)
3. Run /pr-feedback → suppressions.json auto-populated
4. Commit suppressions.json (or keep it global)
5. Next /pr-review → suppressed findings excluded; appear only in 🔕 summary section
```

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
