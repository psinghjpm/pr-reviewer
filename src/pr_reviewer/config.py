"""Config loading: YAML file with environment variable overrides."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml

from pr_reviewer.models import (
    AnthropicConfig,
    AppConfig,
    BitbucketConfig,
    CacheConfig,
    GitHubConfig,
    ReviewConfig,
    Severity,
)


def _env(key: str, default: str = "") -> str:
    return os.environ.get(key, default)


def load_config(config_path: str | Path | None = None) -> AppConfig:
    """Load configuration from YAML file, then apply env-var overrides.

    Priority (highest first):
        1. Environment variables
        2. config.yaml values
        3. Pydantic model defaults
    """
    raw: dict[str, Any] = {}

    if config_path is not None:
        path = Path(config_path)
        if path.exists():
            with path.open() as f:
                raw = yaml.safe_load(f) or {}

    # --- Anthropic ---
    anthropic_raw = raw.get("anthropic", {})
    anthropic = AnthropicConfig(
        api_key=_env("ANTHROPIC_API_KEY") or anthropic_raw.get("api_key", ""),
        model=_env("PR_REVIEWER_MODEL") or anthropic_raw.get("model", "claude-sonnet-4-6"),
        max_tool_calls=int(
            _env("PR_REVIEWER_MAX_TOOL_CALLS")
            or anthropic_raw.get("max_tool_calls", 60)
        ),
    )

    # --- GitHub ---
    gh_raw = raw.get("github", {})
    github = GitHubConfig(
        token=_env("GITHUB_TOKEN") or gh_raw.get("token", ""),
    )

    # --- Bitbucket ---
    bb_raw = raw.get("bitbucket", {})
    bitbucket = BitbucketConfig(
        username=_env("BITBUCKET_USERNAME") or bb_raw.get("username", ""),
        app_password=_env("BITBUCKET_APP_PASSWORD") or bb_raw.get("app_password", ""),
    )

    # --- Review settings ---
    review_raw = raw.get("review", {})
    min_sev_str = (
        _env("PR_REVIEWER_MIN_SEVERITY")
        or review_raw.get("min_severity_to_post", "LOW")
    )
    review = ReviewConfig(
        min_severity_to_post=Severity(min_sev_str.upper()),
        max_inline_comments=int(
            _env("PR_REVIEWER_MAX_INLINE") or review_raw.get("max_inline_comments", 30)
        ),
        max_content_length=int(
            _env("PR_REVIEWER_MAX_CONTENT_LEN")
            or review_raw.get("max_content_length", 12000)
        ),
        cache_ttl_seconds=int(
            _env("PR_REVIEWER_CACHE_TTL")
            or review_raw.get("cache_ttl_seconds", 300)
        ),
    )

    # --- Cache ---
    cache_raw = raw.get("cache", {})
    cache = CacheConfig(
        directory=_env("PR_REVIEWER_CACHE_DIR") or cache_raw.get("directory", ".pr_reviewer_cache"),
        ttl_seconds=int(
            _env("PR_REVIEWER_CACHE_TTL") or cache_raw.get("ttl_seconds", 300)
        ),
    )

    return AppConfig(
        anthropic=anthropic,
        github=github,
        bitbucket=bitbucket,
        review=review,
        cache=cache,
    )


def generate_example_config() -> str:
    """Return the contents of config.example.yaml as a string."""
    here = Path(__file__).parent.parent.parent
    example = here / "config.example.yaml"
    if example.exists():
        return example.read_text()
    return ""
