"""Abstract platform adapter interface."""

from __future__ import annotations

from abc import ABC, abstractmethod

from pr_reviewer.models import FileDiff, PRMetadata


class PlatformAdapter(ABC):
    """Abstract base class that all platform adapters must implement.

    Both GitHub and Bitbucket adapters implement this interface so the agent
    and output layers are platform-agnostic.
    """

    # ------------------------------------------------------------------
    # PR data
    # ------------------------------------------------------------------

    @abstractmethod
    def get_pr_metadata(self, pr_id: int | str) -> PRMetadata:
        """Return metadata for the given PR."""
        ...

    @abstractmethod
    def get_pr_diff(self, pr_id: int | str) -> list[FileDiff]:
        """Return parsed diffs for the given PR."""
        ...

    # ------------------------------------------------------------------
    # File / repo access
    # ------------------------------------------------------------------

    @abstractmethod
    def get_file_content(self, path: str, ref: str) -> str:
        """Return the full content of *path* at *ref* (SHA or branch name).

        Returns empty string if file not found.
        Cached for cache_ttl_seconds (300 s by default).
        """
        ...

    @abstractmethod
    def list_repo_files(self, ref: str, pattern: str = "**/*.py") -> list[str]:
        """Return all file paths in the repo matching *pattern* at *ref*.

        For GitHub: uses the Git tree API.
        For Bitbucket: uses recursive /src endpoint.
        """
        ...

    @abstractmethod
    def search_repo_code(self, query: str, file_pattern: str = "*.py") -> list[dict]:
        """Search the repo for *query* and return a list of match dicts.

        Each dict has keys: path, line, snippet.

        GitHub: uses Code Search API (rate-limited).
        Bitbucket: local grep over cached files (no search API available).
        Capped at 50 results.
        """
        ...

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    @abstractmethod
    def post_inline_comment(
        self,
        pr_id: int | str,
        path: str,
        line: int,
        body: str,
    ) -> None:
        """Post an inline review comment on *line* of *path* in the PR diff."""
        ...

    @abstractmethod
    def post_pr_summary(self, pr_id: int | str, body: str) -> None:
        """Post a top-level (non-inline) comment on the PR."""
        ...

    @abstractmethod
    def get_existing_comments(self, pr_id: int | str) -> list[dict]:
        """Return existing PR comments for deduplication.

        Each dict has at minimum: path, line, body.
        """
        ...

    # ------------------------------------------------------------------
    # Git history
    # ------------------------------------------------------------------

    @abstractmethod
    def get_recent_commits(self, path: str, limit: int = 10) -> list[dict]:
        """Return recent commit log for *path*.

        Each dict: sha, message, author, date.
        """
        ...
