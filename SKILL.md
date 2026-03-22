# PRGenie Skills Documentation

This file documents the Claude Code skills available for PRGenie and provides enterprise integration patterns.

---

## Available Skills

### 1. `/pr-review` — Full PR Code Review

**Location:** `~/.claude/skills/pr-review/SKILL.md`

**Usage:**
```bash
/pr-review https://github.com/owner/repo/pull/123
/pr-review https://github.com/owner/repo/pull/123 --dry-run
/pr-review https://github.com/owner/repo/pull/123 --min-severity HIGH
```

**Configuration:**
- Uses Claude Pro subscription (no API key needed)
- Enterprise config: See [config.enterprise.yaml](config.enterprise.yaml)
- Deterministic mode: temperature=0.0, min_confidence=0.8

### 2. `/pr-feedback` — Harvest Suppressions

**Location:** `~/.claude/skills/pr-feedback/SKILL.md`

**Usage:**
```bash
/pr-feedback https://github.com/owner/repo/pull/123
/pr-feedback https://github.com/owner/repo/pull/123 --dry-run
/pr-feedback https://github.com/owner/repo/pull/123 --output .pr-reviewer/suppressions.json
```

**Purpose:** Extract dismissed findings from PR comments to populate suppressions.json

---

## Enterprise Integration Entry Point

### For Custom Enterprise Implementations

If you're building a custom enterprise wrapper around PRGenie (e.g., for your infrastructure, custom webhooks, or proprietary review logic), use the following entry points:

#### Python API Entry Point

```python
from pr_reviewer.agent.reviewer import PRReviewer
from pr_reviewer.platforms.github_adapter import GitHubAdapter
from pr_reviewer.utils.cache import ReviewCache
from pr_reviewer.context.repo_context_store import RepoContextStore

# 1. Create platform adapter
cache = ReviewCache(directory=".pr_reviewer_cache", ttl=600)
adapter = GitHubAdapter(
    token="ghp_...",
    repo_full_name="owner/repo",
    cache=cache
)

# 2. Load repo context (optional, for convention-aware reviews)
repo_context = RepoContextStore.find(
    repo_full_name="owner/repo",
    local_root=Path.cwd()
)

# 3. Create reviewer with enterprise config
reviewer = PRReviewer(
    adapter=adapter,
    api_key="sk-ant-...",  # Anthropic API key
    model="claude-sonnet-4-6",
    max_tool_calls=40,  # Enterprise: 40 for speed (default: 60)
    max_content_length=10000,  # Enterprise: 10K for speed (default: 12K)
    repo_context=repo_context,
    temperature=0.0,  # CRITICAL: Deterministic mode
)

# 4. Run review
session = reviewer.review(pr_id=123)

# 5. Filter and post findings
from pr_reviewer.output.poster import CommentPoster

poster = CommentPoster(
    adapter=adapter,
    min_severity=Severity.MEDIUM,  # Enterprise: MEDIUM (filter LOW/INFO)
    max_inline_comments=20,  # Enterprise: 20 (less overwhelming)
    dry_run=False,
    min_confidence=0.8,  # CRITICAL: Filter false positives
)

stats = poster.post(session)

# 6. Check results
print(f"Posted {stats['posted_inline']} findings")
print(f"Filtered {stats['skipped_low_confidence']} low-confidence findings")
```

#### Configuration-Driven Entry Point

```python
from pr_reviewer.config import load_config
from pr_reviewer.cli import review

# Load enterprise config
config = load_config("config.enterprise.yaml")

# Run review programmatically
# (Note: CLI wraps this logic - see src/pr_reviewer/cli.py for details)
```

#### Key Entry Point Files

| File | Purpose | Use For |
|------|---------|---------|
| [src/pr_reviewer/agent/reviewer.py](src/pr_reviewer/agent/reviewer.py) | Core `PRReviewer` class | Custom review logic |
| [src/pr_reviewer/platforms/base.py](src/pr_reviewer/platforms/base.py) | `PlatformAdapter` interface | Custom platforms (GitLab, Azure DevOps) |
| [src/pr_reviewer/output/poster.py](src/pr_reviewer/output/poster.py) | `CommentPoster` class | Custom filtering/posting |
| [src/pr_reviewer/config.py](src/pr_reviewer/config.py) | Config loader | Load your enterprise config |
| [src/pr_reviewer/cli.py](src/pr_reviewer/cli.py) | CLI entry point | Wrap in your custom CLI |

---

## Enterprise Configuration Reference

### Critical Settings for Determinism & Quality

```yaml
anthropic:
  # CRITICAL: Set to 0.0 for deterministic reviews
  temperature: 0.0

  # Speed optimization: Lower = faster (default: 60)
  max_tool_calls: 40

review:
  # CRITICAL: Set to 0.8 to cut false positives by 50-80%
  min_confidence_to_post: 0.8

  # Filter noise: MEDIUM = skip LOW/INFO findings
  min_severity_to_post: MEDIUM

  # Less overwhelming: 20 vs 30 default
  max_inline_comments: 20

  # Speed optimization: 10K vs 12K default
  max_content_length: 10000
```

**See:** [config.enterprise.yaml](config.enterprise.yaml) for full reference

---

## Custom Platform Adapter Pattern

### Example: GitLab Adapter

```python
from pr_reviewer.platforms.base import PlatformAdapter
from pr_reviewer.models import PRMetadata, FileDiff, Platform

class GitLabAdapter(PlatformAdapter):
    """Custom adapter for GitLab."""

    def __init__(self, token: str, project_id: str, cache=None):
        self._token = token
        self._project_id = project_id
        self._cache = cache
        self._platform = Platform.GITLAB  # Add to enum in models.py

    def get_pr_metadata(self, pr_id: int | str) -> PRMetadata:
        # Fetch from GitLab API
        response = requests.get(
            f"https://gitlab.com/api/v4/projects/{self._project_id}/merge_requests/{pr_id}",
            headers={"Authorization": f"Bearer {self._token}"}
        )
        data = response.json()

        return PRMetadata(
            pr_id=pr_id,
            title=data["title"],
            description=data["description"],
            author=data["author"]["username"],
            source_branch=data["source_branch"],
            target_branch=data["target_branch"],
            base_sha=data["diff_refs"]["base_sha"],
            head_sha=data["diff_refs"]["head_sha"],
            platform=self._platform,
            repo_full_name=f"{self._project_id}",
            url=data["web_url"],
        )

    def get_pr_diff(self, pr_id: int | str) -> list[FileDiff]:
        # Fetch diff and parse into FileDiff objects
        # Implementation details...
        pass

    def get_file_content(self, path: str, ref: str) -> str:
        # Fetch file content at specific ref
        pass

    def post_inline_comment(self, pr_id: int | str, file: str, line: int, body: str) -> None:
        # Post comment via GitLab API
        pass

    def post_pr_summary(self, pr_id: int | str, body: str) -> None:
        # Post summary comment
        pass

    # Implement other required methods...
```

**Usage:**
```python
adapter = GitLabAdapter(token="glpat-...", project_id="12345")
reviewer = PRReviewer(adapter=adapter, api_key="sk-ant-...")
session = reviewer.review(pr_id=456)
```

---

## Webhook Integration Pattern

### Enterprise Webhook Handler

```python
from fastapi import FastAPI, Request, HTTPException
from pr_reviewer.agent.reviewer import PRReviewer
from pr_reviewer.platforms.github_adapter import GitHubAdapter

app = FastAPI()

@app.post("/webhook/github")
async def handle_github_webhook(request: Request):
    """Handle GitHub PR webhook events."""

    payload = await request.json()

    # Only process PR opened/synchronized events
    if payload.get("action") not in ["opened", "synchronize"]:
        return {"status": "ignored"}

    pr_number = payload["pull_request"]["number"]
    repo_full_name = payload["repository"]["full_name"]

    # Create adapter
    adapter = GitHubAdapter(
        token=os.getenv("GITHUB_TOKEN"),
        repo_full_name=repo_full_name
    )

    # Create reviewer with ENTERPRISE CONFIG
    reviewer = PRReviewer(
        adapter=adapter,
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        temperature=0.0,  # CRITICAL
        max_tool_calls=40,  # SPEED
    )

    # Run review
    session = reviewer.review(pr_id=pr_number)

    # Post with ENTERPRISE FILTERS
    poster = CommentPoster(
        adapter=adapter,
        min_confidence=0.8,  # CRITICAL
        min_severity=Severity.MEDIUM,
        max_inline_comments=20,
    )

    stats = poster.post(session)

    return {"status": "reviewed", "findings": stats["posted_inline"]}
```

**Deploy with:**
- Kubernetes: See [deploy/k8s/](deploy/k8s/)
- AWS Lambda: Wrap in Lambda handler
- Azure Functions: Wrap in Azure handler

---

## Testing Your Enterprise Integration

### Unit Test Pattern

```python
import pytest
from unittest.mock import MagicMock
from pr_reviewer.agent.reviewer import PRReviewer

def test_enterprise_review_determinism():
    """Test that reviews are deterministic with temperature=0.0."""

    # Mock adapter
    adapter = MagicMock()
    adapter.get_pr_metadata.return_value = PRMetadata(...)
    adapter.get_pr_diff.return_value = [FileDiff(...)]

    # Create reviewer with enterprise config
    reviewer = PRReviewer(
        adapter=adapter,
        api_key="test-key",
        temperature=0.0,  # CRITICAL
        max_tool_calls=40,
    )

    # Run twice
    session1 = reviewer.review(pr_id=123)
    session2 = reviewer.review(pr_id=123)

    # Assert determinism
    assert len(session1.findings) == len(session2.findings)
    for f1, f2 in zip(session1.findings, session2.findings):
        assert f1.file == f2.file
        assert f1.line_start == f2.line_start
        assert f1.severity == f2.severity
        assert f1.confidence == f2.confidence
```

### Integration Test Pattern

```python
def test_enterprise_filters():
    """Test that enterprise filters work correctly."""

    # Create test findings
    findings = [
        ReviewFinding(severity=Severity.HIGH, confidence=0.92, ...),
        ReviewFinding(severity=Severity.MEDIUM, confidence=0.75, ...),  # Below 0.8
        ReviewFinding(severity=Severity.LOW, confidence=0.85, ...),  # Below MEDIUM
    ]

    # Filter with enterprise settings
    poster = CommentPoster(
        adapter=adapter,
        min_confidence=0.8,
        min_severity=Severity.MEDIUM,
    )

    # Should only post finding #1
    # Finding #2 filtered by confidence
    # Finding #3 filtered by severity
```

---

## Quick Start for Your Enterprise

### Step 1: Clone Core Logic

```python
# your_enterprise_reviewer.py
from pr_reviewer.agent.reviewer import PRReviewer
from pr_reviewer.config import load_config

class EnterpriseReviewer(PRReviewer):
    """Your custom enterprise wrapper."""

    def __init__(self, *args, **kwargs):
        # Load your enterprise config
        config = load_config("your_enterprise_config.yaml")

        # Override defaults with enterprise values
        kwargs.setdefault("temperature", config.anthropic.temperature)
        kwargs.setdefault("max_tool_calls", config.anthropic.max_tool_calls)

        super().__init__(*args, **kwargs)

    def review(self, pr_id: int | str):
        # Add your custom pre-processing
        self._log_to_your_system(f"Starting review for PR {pr_id}")

        # Run core review
        session = super().review(pr_id)

        # Add your custom post-processing
        self._send_metrics_to_your_dashboard(session)

        return session
```

### Step 2: Use Core Config

```yaml
# your_enterprise_config.yaml
# Start with config.enterprise.yaml and customize
anthropic:
  temperature: 0.0  # Keep this!
  max_tool_calls: 40  # Tune for your needs

review:
  min_confidence_to_post: 0.8  # Keep this!
  min_severity_to_post: MEDIUM  # Or HIGH for more aggressive filtering

# Add your custom settings
your_enterprise:
  metrics_endpoint: https://your-metrics.internal
  slack_webhook: https://hooks.slack.com/...
```

### Step 3: Deploy

See [docs/ENTERPRISE_QUICK_START.md](docs/ENTERPRISE_QUICK_START.md) for deployment guide.

---

## Key Files for Enterprise Integration

### Core Logic (Import These)

```
src/pr_reviewer/
├── agent/
│   ├── reviewer.py          ← PRReviewer class (MAIN ENTRY POINT)
│   ├── tool_definitions.py  ← Review tools (customize if needed)
│   └── tool_executor.py     ← Context gathering (customize if needed)
├── platforms/
│   ├── base.py              ← PlatformAdapter interface (IMPLEMENT FOR CUSTOM PLATFORMS)
│   ├── github_adapter.py    ← GitHub implementation (REFERENCE)
│   └── bitbucket_adapter.py ← Bitbucket implementation (REFERENCE)
├── output/
│   ├── poster.py            ← CommentPoster class (customize filters)
│   └── formatter.py         ← Comment formatting (customize style)
├── config.py                ← Config loader (LOAD YOUR CONFIG)
└── models.py                ← Data models (Pydantic v2)
```

### Enterprise Reference (Copy & Customize)

```
config.enterprise.yaml       ← PRODUCTION CONFIG (COPY THIS)
docs/
├── ENTERPRISE_QUICK_START.md  ← Deployment guide
├── QUICK_WINS_SUMMARY.md      ← Technical details
└── determinism-analysis.md    ← Deep-dive on determinism
```

---

## Support & Documentation

- **Quick Start:** [README_QUICK_WINS.md](README_QUICK_WINS.md)
- **Deployment:** [docs/ENTERPRISE_QUICK_START.md](docs/ENTERPRISE_QUICK_START.md)
- **Configuration:** [config.enterprise.yaml](config.enterprise.yaml)
- **Testing:** [TESTING_DETERMINISM.md](TESTING_DETERMINISM.md)
- **API Reference:** See docstrings in source files

---

**Last Updated:** 2026-03-21
**Version:** 1.0 (Enterprise Quick Wins)
