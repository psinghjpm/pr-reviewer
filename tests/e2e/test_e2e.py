"""End-to-end tests against real APIs. Gated by RUN_E2E=1 env var."""

from __future__ import annotations

import os

import pytest

# Guard: skip unless RUN_E2E=1 is explicitly set
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_E2E") != "1",
    reason="E2E tests skipped. Set RUN_E2E=1 to run.",
)


@pytest.mark.e2e
def test_github_pr_dry_run():
    """Dry-run review of a real GitHub PR (no comments posted)."""
    from pr_reviewer.agent.reviewer import PRReviewer
    from pr_reviewer.config import load_config
    from pr_reviewer.output.poster import CommentPoster
    from pr_reviewer.platforms.github_adapter import GitHubAdapter

    repo = os.environ.get("E2E_GITHUB_REPO", "")
    pr_id = int(os.environ.get("E2E_GITHUB_PR", "0"))
    assert repo and pr_id, "Set E2E_GITHUB_REPO and E2E_GITHUB_PR for e2e tests."

    cfg = load_config(None)
    assert cfg.github.token, "GITHUB_TOKEN required for e2e tests."
    assert cfg.anthropic.api_key, "ANTHROPIC_API_KEY required for e2e tests."

    from pr_reviewer.utils.cache import ReviewCache
    cache = ReviewCache()
    adapter = GitHubAdapter(token=cfg.github.token, repo_full_name=repo, cache=cache)

    reviewer = PRReviewer(
        adapter=adapter,
        api_key=cfg.anthropic.api_key,
        model=cfg.anthropic.model,
        max_tool_calls=20,  # reduced for e2e speed
    )
    session = reviewer.review(pr_id)

    assert session.pr_metadata.pr_id == pr_id
    assert isinstance(session.findings, list)
    assert session.summary is not None

    # Dry-run post
    poster = CommentPoster(adapter=adapter, dry_run=True)
    stats = poster.post(session)
    assert stats["dry_run"] is True
    assert stats["posted_inline"] == 0


@pytest.mark.e2e
def test_bitbucket_pr_dry_run():
    """Dry-run review of a real Bitbucket PR (no comments posted)."""
    from pr_reviewer.agent.reviewer import PRReviewer
    from pr_reviewer.config import load_config
    from pr_reviewer.output.poster import CommentPoster
    from pr_reviewer.platforms.bitbucket_adapter import BitbucketAdapter

    workspace = os.environ.get("E2E_BB_WORKSPACE", "")
    repo = os.environ.get("E2E_BB_REPO", "")
    pr_id = int(os.environ.get("E2E_BB_PR", "0"))
    assert workspace and repo and pr_id, "Set E2E_BB_* env vars for e2e tests."

    cfg = load_config(None)
    assert cfg.bitbucket.username and cfg.bitbucket.app_password, "Bitbucket credentials required."
    assert cfg.anthropic.api_key, "ANTHROPIC_API_KEY required."

    from pr_reviewer.utils.cache import ReviewCache
    cache = ReviewCache()
    adapter = BitbucketAdapter(
        username=cfg.bitbucket.username,
        app_password=cfg.bitbucket.app_password,
        workspace=workspace,
        repo_slug=repo,
        cache=cache,
    )

    reviewer = PRReviewer(
        adapter=adapter,
        api_key=cfg.anthropic.api_key,
        model=cfg.anthropic.model,
        max_tool_calls=20,
    )
    session = reviewer.review(pr_id)

    assert session.pr_metadata.pr_id == pr_id
    poster = CommentPoster(adapter=adapter, dry_run=True)
    stats = poster.post(session)
    assert stats["dry_run"] is True
