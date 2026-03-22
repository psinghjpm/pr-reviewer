# Enterprise Integration Guide

**Quick reference for integrating PRGenie into your custom enterprise infrastructure**

---

## 🎯 What's Been Committed

✅ **Core implementation** (5 Python files):
- Temperature control (deterministic reviews)
- Confidence filtering (reduce false positives)
- Enhanced prompts (actionable suggestions)
- Speed optimizations

✅ **Enterprise configuration**:
- [config.enterprise.yaml](config.enterprise.yaml) — Production-ready defaults
- [SKILL.md](SKILL.md) — Entry points + integration patterns

✅ **Comprehensive documentation** (4 guides):
- [docs/ENTERPRISE_QUICK_START.md](docs/ENTERPRISE_QUICK_START.md) — 30-min deployment
- [docs/QUICK_WINS_SUMMARY.md](docs/QUICK_WINS_SUMMARY.md) — Technical details
- [docs/determinism-analysis.md](docs/determinism-analysis.md) — Deep-dive
- [README_QUICK_WINS.md](README_QUICK_WINS.md) — Quick start

---

## 🚀 Entry Points for Your Enterprise Implementation

### Primary Entry Point: `PRReviewer` Class

```python
from pr_reviewer.agent.reviewer import PRReviewer
from pr_reviewer.platforms.github_adapter import GitHubAdapter

# Create your custom adapter (or use GitHubAdapter)
adapter = GitHubAdapter(
    token="ghp_...",
    repo_full_name="owner/repo"
)

# Create reviewer with ENTERPRISE SETTINGS
reviewer = PRReviewer(
    adapter=adapter,
    api_key="sk-ant-...",
    model="claude-sonnet-4-6",
    temperature=0.0,          # ← CRITICAL: Deterministic
    max_tool_calls=40,        # ← Speed optimization
    max_content_length=10000, # ← Speed optimization
    repo_context=None,        # Optional: Load from RepoContextStore
)

# Run review
session = reviewer.review(pr_id=123)

# Access findings
for finding in session.findings:
    print(f"{finding.severity} at {finding.file}:{finding.line_start}")
    print(f"  {finding.message}")
    print(f"  Confidence: {finding.confidence}")
```

### Secondary Entry Point: `CommentPoster` Class

```python
from pr_reviewer.output.poster import CommentPoster
from pr_reviewer.models import Severity

# Filter and post with ENTERPRISE SETTINGS
poster = CommentPoster(
    adapter=adapter,
    min_severity=Severity.MEDIUM,  # ← Filter LOW/INFO
    min_confidence=0.8,            # ← CRITICAL: Reduce false positives
    max_inline_comments=20,        # ← Less overwhelming
    dry_run=False,
)

# Post findings
stats = poster.post(session)

print(f"Posted: {stats['posted_inline']}")
print(f"Filtered (low confidence): {stats['skipped_low_confidence']}")
print(f"Filtered (low severity): {stats['skipped_low_severity']}")
```

---

## 🔧 Custom Platform Adapter

### Implementing for GitLab, Azure DevOps, etc.

```python
from pr_reviewer.platforms.base import PlatformAdapter
from pr_reviewer.models import PRMetadata, FileDiff, Platform

class YourCustomAdapter(PlatformAdapter):
    """Adapter for your enterprise platform."""

    def get_pr_metadata(self, pr_id: int | str) -> PRMetadata:
        # Fetch from your platform API
        return PRMetadata(
            pr_id=pr_id,
            title="...",
            description="...",
            author="...",
            source_branch="...",
            target_branch="...",
            base_sha="...",
            head_sha="...",
            platform=Platform.GITHUB,  # Or add custom to enum
            repo_full_name="...",
            url="...",
        )

    def get_pr_diff(self, pr_id: int | str) -> list[FileDiff]:
        # Parse diff from your platform
        # See GitHubAdapter for reference implementation
        pass

    def get_file_content(self, path: str, ref: str) -> str:
        # Fetch file content at specific commit
        pass

    def post_inline_comment(self, pr_id: int | str, file: str, line: int, body: str) -> None:
        # Post inline comment via your platform API
        pass

    def post_pr_summary(self, pr_id: int | str, body: str) -> None:
        # Post summary comment
        pass

    # Implement remaining abstract methods (see base.py)
```

**Reference implementations:**
- [src/pr_reviewer/platforms/github_adapter.py](src/pr_reviewer/platforms/github_adapter.py)
- [src/pr_reviewer/platforms/bitbucket_adapter.py](src/pr_reviewer/platforms/bitbucket_adapter.py)

---

## 📦 Wrapper Pattern for Your Infrastructure

### Option 1: Subclass `PRReviewer`

```python
from pr_reviewer.agent.reviewer import PRReviewer
from pr_reviewer.config import load_config

class YourEnterpriseReviewer(PRReviewer):
    """Wrapper that enforces your enterprise standards."""

    def __init__(self, adapter, **kwargs):
        # Load your enterprise config
        config = load_config("your_config.yaml")

        # Force enterprise settings (cannot be overridden)
        kwargs["temperature"] = 0.0  # Always deterministic
        kwargs["max_tool_calls"] = config.anthropic.max_tool_calls

        super().__init__(
            adapter=adapter,
            api_key=config.anthropic.api_key,
            **kwargs
        )

    def review(self, pr_id: int | str):
        # Pre-processing: Log to your monitoring system
        self._log_review_start(pr_id)

        # Run core review
        try:
            session = super().review(pr_id)

            # Post-processing: Send metrics to your dashboard
            self._send_metrics(session)

            return session

        except Exception as e:
            self._log_error(pr_id, e)
            raise

    def _log_review_start(self, pr_id):
        # Send to your logging system (DataDog, Splunk, etc.)
        pass

    def _send_metrics(self, session):
        # Send to your metrics system
        # metrics.gauge("pr_review.findings", len(session.findings))
        # metrics.gauge("pr_review.high_severity", ...)
        pass

    def _log_error(self, pr_id, error):
        # Send to your error tracking (Sentry, Rollbar, etc.)
        pass
```

### Option 2: Composition Pattern

```python
class YourEnterpriseReviewService:
    """Service layer that wraps PRGenie."""

    def __init__(self, config_path: str):
        self.config = load_config(config_path)
        self._init_monitoring()

    def review_pr(self, platform: str, repo: str, pr_id: int):
        """High-level review method for your enterprise."""

        # 1. Create adapter based on platform
        adapter = self._create_adapter(platform, repo)

        # 2. Load repo context
        repo_context = self._load_repo_context(repo)

        # 3. Create reviewer with ENFORCED enterprise settings
        reviewer = PRReviewer(
            adapter=adapter,
            api_key=self.config.anthropic.api_key,
            temperature=0.0,  # ENFORCED
            max_tool_calls=40,  # ENFORCED
            repo_context=repo_context,
        )

        # 4. Run review
        session = reviewer.review(pr_id)

        # 5. Filter with ENFORCED enterprise settings
        poster = CommentPoster(
            adapter=adapter,
            min_confidence=0.8,  # ENFORCED
            min_severity=Severity.MEDIUM,  # ENFORCED
            max_inline_comments=20,
        )

        # 6. Post findings
        stats = poster.post(session)

        # 7. Send to your systems
        self._send_to_slack(repo, pr_id, stats)
        self._send_to_metrics(stats)

        return stats

    def _create_adapter(self, platform: str, repo: str):
        if platform == "github":
            return GitHubAdapter(token=self.config.github.token, repo_full_name=repo)
        elif platform == "gitlab":
            return YourGitLabAdapter(token=self.config.gitlab.token, project_id=repo)
        else:
            raise ValueError(f"Unsupported platform: {platform}")

    def _load_repo_context(self, repo: str):
        from pr_reviewer.context.repo_context_store import RepoContextStore
        return RepoContextStore.find(repo_full_name=repo, local_root=Path.cwd())

    def _send_to_slack(self, repo, pr_id, stats):
        # Post summary to your Slack channel
        pass

    def _send_to_metrics(self, stats):
        # Send to DataDog, Prometheus, etc.
        pass
```

**Usage:**
```python
service = YourEnterpriseReviewService("your_config.yaml")
service.review_pr(platform="github", repo="owner/repo", pr_id=123)
```

---

## 🔐 Configuration Management

### Your Enterprise Config

```yaml
# your_enterprise_config.yaml

# Core PRGenie settings (DO NOT CHANGE THESE)
anthropic:
  api_key: ""  # Load from secrets manager
  temperature: 0.0  # ENFORCED - do not change
  max_tool_calls: 40  # Tune for your infrastructure

review:
  min_confidence_to_post: 0.8  # ENFORCED - do not change
  min_severity_to_post: MEDIUM  # Or HIGH for stricter filtering
  max_inline_comments: 20

# Your custom enterprise settings
your_enterprise:
  monitoring:
    datadog_api_key: ""
    sentry_dsn: ""

  notifications:
    slack_webhook: ""
    email_domain: "@yourcompany.com"

  compliance:
    require_jira_ticket: true
    allowed_repos:
      - "yourorg/*"
      - "yourteam/*"
```

### Loading Config in Your Code

```python
import yaml
from pathlib import Path
from pr_reviewer.config import load_config as load_prgenie_config

def load_your_enterprise_config(path: str):
    """Load both PRGenie config and your custom settings."""

    # Load PRGenie base config
    prgenie_config = load_prgenie_config(path)

    # Load your custom settings
    with open(path) as f:
        full_config = yaml.safe_load(f)

    return {
        "prgenie": prgenie_config,
        "enterprise": full_config.get("your_enterprise", {}),
    }
```

---

## 🐳 Deployment Patterns

### Docker Container

```dockerfile
# Dockerfile.enterprise
FROM python:3.12-slim

WORKDIR /app

# Install PRGenie
COPY pyproject.toml .
RUN pip install -e .

# Copy your enterprise wrapper
COPY your_enterprise_reviewer.py .
COPY your_enterprise_config.yaml .

# Entry point
CMD ["python", "your_enterprise_reviewer.py"]
```

### Kubernetes CronJob (Scheduled Reviews)

```yaml
# k8s/review-cronjob.yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: pr-review-scheduled
spec:
  schedule: "*/15 * * * *"  # Every 15 minutes
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: reviewer
            image: your-registry/prgenie-enterprise:latest
            env:
            - name: ANTHROPIC_API_KEY
              valueFrom:
                secretKeyRef:
                  name: prgenie-secrets
                  key: anthropic-api-key
            - name: GITHUB_TOKEN
              valueFrom:
                secretKeyRef:
                  name: prgenie-secrets
                  key: github-token
            volumeMounts:
            - name: config
              mountPath: /app/config.yaml
              subPath: config.yaml
          volumes:
          - name: config
            configMap:
              name: prgenie-enterprise-config
          restartPolicy: OnFailure
```

### AWS Lambda Handler

```python
# lambda_handler.py
import json
from your_enterprise_reviewer import YourEnterpriseReviewService

service = YourEnterpriseReviewService("config.yaml")

def lambda_handler(event, context):
    """AWS Lambda handler for GitHub webhooks."""

    # Parse webhook payload
    body = json.loads(event["body"])

    if body.get("action") not in ["opened", "synchronize"]:
        return {"statusCode": 200, "body": "Ignored"}

    pr_number = body["pull_request"]["number"]
    repo = body["repository"]["full_name"]

    # Run review
    stats = service.review_pr(platform="github", repo=repo, pr_id=pr_number)

    return {
        "statusCode": 200,
        "body": json.dumps({"findings": stats["posted_inline"]})
    }
```

---

## 📊 Metrics & Monitoring

### Key Metrics to Track

```python
class EnterpriseMetrics:
    """Send metrics to your monitoring system."""

    @staticmethod
    def track_review(session, stats):
        """Track review metrics."""

        # Review performance
        metrics.gauge("pr_review.duration_seconds", session.duration)
        metrics.gauge("pr_review.tool_calls", session.tool_call_count)

        # Finding quality
        metrics.gauge("pr_review.findings.total", len(session.findings))
        metrics.gauge("pr_review.findings.posted", stats["posted_inline"])
        metrics.gauge("pr_review.findings.filtered_confidence", stats["skipped_low_confidence"])
        metrics.gauge("pr_review.findings.filtered_severity", stats["skipped_low_severity"])

        # Finding breakdown by severity
        for severity in ["CRITICAL", "HIGH", "MEDIUM", "LOW"]:
            count = sum(1 for f in session.findings if f.severity.value == severity)
            metrics.gauge(f"pr_review.findings.{severity.lower()}", count)

        # Confidence distribution
        avg_confidence = sum(f.confidence for f in session.findings) / len(session.findings) if session.findings else 0
        metrics.gauge("pr_review.confidence.average", avg_confidence)

        # Cost tracking
        estimated_cost = 0.25  # Estimate based on tokens used
        metrics.gauge("pr_review.cost_usd", estimated_cost)
```

---

## ✅ Testing Your Integration

### Unit Test

```python
import pytest
from your_enterprise_reviewer import YourEnterpriseReviewService

def test_enterprise_settings_enforced():
    """Test that enterprise settings cannot be overridden."""

    service = YourEnterpriseReviewService("test_config.yaml")

    # Internal reviewer should have enforced settings
    # (Access internal state for testing)
    assert service._reviewer_settings["temperature"] == 0.0
    assert service._poster_settings["min_confidence"] == 0.8
```

### Integration Test

```python
def test_full_review_workflow(mock_github_api):
    """Test full review workflow end-to-end."""

    service = YourEnterpriseReviewService("test_config.yaml")

    # Mock GitHub API responses
    mock_github_api.setup_pr_data(pr_id=123, ...)

    # Run review
    stats = service.review_pr(platform="github", repo="test/repo", pr_id=123)

    # Assert results
    assert stats["posted_inline"] >= 0
    assert stats["skipped_low_confidence"] >= 0

    # Assert monitoring was called
    assert mock_metrics.gauge.called_with("pr_review.findings.total")
```

---

## 📚 Key Files Reference

### Entry Points (Import These)

| File | Class/Function | Purpose |
|------|----------------|---------|
| `src/pr_reviewer/agent/reviewer.py` | `PRReviewer` | Main review logic |
| `src/pr_reviewer/output/poster.py` | `CommentPoster` | Filter & post findings |
| `src/pr_reviewer/config.py` | `load_config()` | Load configuration |
| `src/pr_reviewer/platforms/base.py` | `PlatformAdapter` | Custom platform interface |

### Configuration

| File | Purpose |
|------|---------|
| `config.enterprise.yaml` | Production-ready defaults (COPY THIS) |
| `SKILL.md` | Integration patterns & examples |

### Documentation

| File | Purpose |
|------|---------|
| `docs/ENTERPRISE_QUICK_START.md` | 30-min deployment guide |
| `docs/QUICK_WINS_SUMMARY.md` | Technical implementation details |
| `README_QUICK_WINS.md` | Quick start & overview |

---

## 🚀 Quick Start for Your Team

1. **Copy config:**
   ```bash
   cp config.enterprise.yaml your_enterprise_config.yaml
   # Edit with your settings
   ```

2. **Create wrapper:**
   ```python
   # your_reviewer.py
   from pr_reviewer.agent.reviewer import PRReviewer
   # ... implement wrapper as shown above
   ```

3. **Test locally:**
   ```bash
   python your_reviewer.py --pr-url https://github.com/owner/repo/pull/123 --dry-run
   ```

4. **Deploy:**
   - See [docs/ENTERPRISE_QUICK_START.md](docs/ENTERPRISE_QUICK_START.md)

---

## 💡 Best Practices

### DO ✅

- ✅ **Keep temperature=0.0** (determinism is critical)
- ✅ **Keep min_confidence=0.8** (reduces false positives 50-80%)
- ✅ **Use min_severity=MEDIUM** (filters noise)
- ✅ **Load repo_context** (for convention-aware reviews)
- ✅ **Monitor metrics** (track quality over time)
- ✅ **Build suppressions.json** (eliminate repeat noise)

### DON'T ❌

- ❌ **Don't change temperature** (breaks determinism)
- ❌ **Don't lower min_confidence below 0.7** (increases false positives)
- ❌ **Don't skip configuration validation** (enforce enterprise standards)
- ❌ **Don't ignore test results** (validate determinism before production)

---

## 📞 Support

- **Entry Points:** See [SKILL.md](SKILL.md)
- **Deployment:** See [docs/ENTERPRISE_QUICK_START.md](docs/ENTERPRISE_QUICK_START.md)
- **Testing:** See [TESTING_DETERMINISM.md](TESTING_DETERMINISM.md)
- **Examples:** See source code in `src/pr_reviewer/`

---

**Last Updated:** 2026-03-21
**Version:** 1.0 Enterprise Quick Wins
**Status:** Production Ready ✅
