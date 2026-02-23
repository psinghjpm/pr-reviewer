"""Thin wrapper that fetches git commit history for changed files."""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pr_reviewer.platforms.base import PlatformAdapter

logger = structlog.get_logger(__name__)


class GitHistory:
    """Fetch and format recent commit history for files via the platform adapter."""

    def __init__(self, adapter: "PlatformAdapter") -> None:
        self._adapter = adapter

    def get_history(self, path: str, limit: int = 10) -> str:
        """Return a human-readable commit log for *path* (up to *limit* entries)."""
        try:
            commits = self._adapter.get_recent_commits(path, limit=limit)
        except Exception as exc:
            logger.warning("git_history_fetch_failed", path=path, error=str(exc))
            return f"Could not fetch git history for {path}: {exc}"

        if not commits:
            return f"No recent commits found for {path}."

        lines = [f"Recent commits for `{path}`:"]
        for c in commits:
            lines.append(f"  {c.get('sha', '?')} {c.get('date', '')} — {c.get('message', '')} ({c.get('author', '')})")
        return "\n".join(lines)

    def get_history_for_files(self, paths: list[str], limit: int = 5) -> dict[str, str]:
        """Return {path: history_text} for multiple files."""
        return {path: self.get_history(path, limit) for path in paths}
