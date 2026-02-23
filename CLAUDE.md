# pr-reviewer — Claude Code Context

## Project Overview
Agentic pull-request review tool powered by Claude. Two modes:
- **`/pr-review` skill** — runs inside Claude Code (free with Claude Pro, no API key)
- **Standalone CLI** (`pr-reviewer`) — runs via Anthropic API key

## Repository Layout

```
pr-reviewer/
├── skill/
│   ├── SKILL.md          # Claude Code skill prompt (install to ~/.claude/skills/pr-review/)
│   └── post_review.py    # Generic GitHub PR comment poster (called by the skill)
├── src/pr_reviewer/      # Python package (standalone CLI mode)
│   ├── cli.py            # Typer CLI entrypoint
│   ├── config.py         # YAML + env-var config (api_key optional)
│   ├── models.py         # All Pydantic v2 models
│   ├── agent/            # PRReviewer agentic loop, tool definitions, tool executor
│   ├── context/          # DependencyTracer, SymbolSearch, RelatedTestFinder, GitHistory
│   ├── output/           # Formatter, Deduplicator, CommentPoster
│   └── platforms/        # Abstract PlatformAdapter + GitHubAdapter + BitbucketAdapter
├── tests/
│   ├── unit/             # Pure unit tests (no network)
│   ├── integration/      # Mock-adapter tests (no network)
│   └── e2e/              # Live network tests (require RUN_E2E=1)
├── config.example.yaml   # All config options with documentation
├── pyproject.toml        # Package metadata + dependencies
└── CLAUDE.md             # This file
```

## Skill Installation (Claude Code mode)

Copy the skill files to your Claude Code skills directory:

```bash
mkdir -p ~/.claude/skills/pr-review
cp skill/SKILL.md ~/.claude/skills/pr-review/SKILL.md
cp skill/post_review.py ~/.claude/skills/pr-review/post_review.py
```

Then inside Claude Code:
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
5. Claude calls `skill/post_review.py` to batch-post all inline comments + summary

### post_review.py (generic posting utility)
Accepts any PR URL and pre-generated finding files — not tied to any specific repo.

```bash
python skill/post_review.py \
  --pr-url https://github.com/owner/repo/pull/42 \
  --comments /tmp/pr_comments.json \
  --summary /tmp/pr_summary.md \
  --gh-path "$(which gh)" \
  [--dry-run]
```

Comments JSON schema:
```json
[
  {
    "path": "src/foo/bar.ts",
    "line": 42,
    "severity": "HIGH",
    "category": "SECURITY",
    "message": "Description",
    "suggestion_lang": "typescript",
    "suggestion": "// corrected code",
    "confidence": 95
  }
]
```

## Standalone CLI (API mode)

```bash
pip install -e .

# Review a GitHub PR
ANTHROPIC_API_KEY=... GITHUB_TOKEN=... \
  pr-reviewer review --url https://github.com/owner/repo/pull/123

# Dry run
pr-reviewer review --url ... --dry-run

# Review a Bitbucket PR
ANTHROPIC_API_KEY=... BITBUCKET_USERNAME=... BITBUCKET_APP_PASSWORD=... \
  pr-reviewer review --platform bitbucket --workspace ws --repo repo --pr 1
```

## Running Tests

```bash
# Fast — unit tests only (no network, no API key)
pytest tests/unit/ -v

# Integration — mock adapter (no network)
pytest tests/integration/ -v

# All non-E2E
pytest tests/unit/ tests/integration/ -v

# E2E (live network — posts real comments)
RUN_E2E=1 E2E_GITHUB_REPO=owner/repo E2E_GITHUB_PR=1 pytest tests/e2e/ -v
```

## Key Decisions & Conventions

- **Pydantic v2**: use `model_config = {"arbitrary_types_allowed": True}` (not `class Config`)
- **Windows paths**: always use `.as_posix()` for repo-relative paths in API calls
- **gh CLI path on Windows**: `C:\Program Files\GitHub CLI\gh.exe` — pass via `--gh-path`
- **Windows console encoding**: `post_review.py` calls `sys.stdout.reconfigure(encoding="utf-8")` at startup to handle emoji on cp1252 terminals
- **Backend auto-detection**: CLI checks for `ANTHROPIC_API_KEY`; if absent, prints `/pr-review` hint
- **Severity ordering**: CRITICAL > HIGH > MEDIUM > LOW > INFO (used for `--min-severity` filtering)
- **Comment deduplication**: existing PR comments are fetched before posting; findings already present are skipped

## Environment Variables

| Variable | Required | Used by |
|---|---|---|
| `ANTHROPIC_API_KEY` | API mode only | Standalone CLI |
| `GITHUB_TOKEN` | GitHub PRs | Both modes |
| `BITBUCKET_USERNAME` | Bitbucket PRs | Both modes |
| `BITBUCKET_APP_PASSWORD` | Bitbucket PRs | Both modes |
