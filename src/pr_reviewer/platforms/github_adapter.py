"""GitHub platform adapter using PyGitHub."""

from __future__ import annotations

import base64
import fnmatch
import re
from typing import Any

import requests
import structlog
from github import Github, GithubException
from github.PullRequest import PullRequest
from github.Repository import Repository

from pr_reviewer.models import FileDiff, Platform, PRMetadata
from pr_reviewer.platforms.base import PlatformAdapter
from pr_reviewer.utils.cache import ReviewCache
from pr_reviewer.utils.diff_parser import parse_diff
from pr_reviewer.utils.rate_limiter import github_rate_limited, github_search_rate_limited, retry_with_backoff

logger = structlog.get_logger(__name__)

_MAX_SEARCH_RESULTS = 50
_MAX_FILE_SIZE = 1_000_000  # 1 MB GitHub API limit


class GitHubAdapter(PlatformAdapter):
    """Pull request adapter for GitHub repositories."""

    def __init__(
        self,
        token: str,
        repo_full_name: str,
        cache: ReviewCache | None = None,
        cache_ttl: int = 300,
    ) -> None:
        self._gh = Github(token)
        self._token = token
        self._repo_name = repo_full_name
        self._repo: Repository | None = None
        self._cache = cache or ReviewCache()
        self._cache_ttl = cache_ttl

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @property
    def repo(self) -> Repository:
        if self._repo is None:
            self._repo = self._gh.get_repo(self._repo_name)
        return self._repo

    def _raw_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"token {self._token}",
            "Accept": "application/vnd.github.v3+json",
        }

    # ------------------------------------------------------------------
    # PR data
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, retryable_exceptions=(GithubException, requests.RequestException))
    def get_pr_metadata(self, pr_id: int | str) -> PRMetadata:
        pr: PullRequest = self.repo.get_pull(int(pr_id))
        return PRMetadata(
            pr_id=pr.number,
            title=pr.title,
            description=pr.body or "",
            author=pr.user.login,
            source_branch=pr.head.ref,
            target_branch=pr.base.ref,
            base_sha=pr.base.sha,
            head_sha=pr.head.sha,
            platform=Platform.GITHUB,
            repo_full_name=self._repo_name,
            url=pr.html_url,
            is_draft=pr.draft,
        )

    @retry_with_backoff(max_retries=3, retryable_exceptions=(GithubException, requests.RequestException))
    def get_pr_diff(self, pr_id: int | str) -> list[FileDiff]:
        pr: PullRequest = self.repo.get_pull(int(pr_id))
        diff_url = pr.diff_url
        resp = requests.get(diff_url, headers=self._raw_headers(), timeout=30)
        resp.raise_for_status()
        return parse_diff(resp.text)

    # ------------------------------------------------------------------
    # File / repo access
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, retryable_exceptions=(GithubException, requests.RequestException))
    def get_file_content(self, path: str, ref: str) -> str:
        cache_key = ReviewCache.make_key("gh_file", self._repo_name, ref, path)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            content_file = self.repo.get_contents(path, ref=ref)
        except GithubException as exc:
            if exc.status == 404:
                return ""
            if exc.status == 403 and "too large" in str(exc).lower():
                # Fall back to raw download URL
                return self._fetch_large_file(path, ref)
            raise

        # content_file may be a list for directories
        if isinstance(content_file, list):
            return ""

        if content_file.encoding == "base64":
            text = base64.b64decode(content_file.content).decode("utf-8", errors="replace")
        else:
            text = content_file.decoded_content.decode("utf-8", errors="replace")

        self._cache.set(cache_key, text, ttl=self._cache_ttl)
        return text

    def _fetch_large_file(self, path: str, ref: str) -> str:
        """Fetch oversized files via the raw blob endpoint."""
        try:
            blob_sha = self.repo.get_git_tree(ref, recursive=False)
            # Use raw GitHub URL
            raw_url = (
                f"https://raw.githubusercontent.com/{self._repo_name}/{ref}/{path}"
            )
            resp = requests.get(raw_url, headers=self._raw_headers(), timeout=30)
            if resp.status_code == 200:
                return resp.text
        except Exception as exc:
            logger.warning("large_file_fetch_failed", path=path, ref=ref, error=str(exc))
        return ""

    @retry_with_backoff(max_retries=2, retryable_exceptions=(GithubException,))
    def list_repo_files(self, ref: str, pattern: str = "**/*.py") -> list[str]:
        cache_key = ReviewCache.make_key("gh_tree", self._repo_name, ref, pattern)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            tree = self.repo.get_git_tree(ref, recursive=True)
        except GithubException:
            return []

        paths = [
            item.path
            for item in tree.tree
            if item.type == "blob" and fnmatch.fnmatch(item.path, pattern)
        ]
        self._cache.set(cache_key, paths, ttl=self._cache_ttl * 6)  # trees change rarely
        return paths

    @github_search_rate_limited
    @retry_with_backoff(max_retries=2, retryable_exceptions=(GithubException, requests.RequestException))
    def search_repo_code(self, query: str, file_pattern: str = "*.py") -> list[dict]:
        """Search via GitHub Code Search API, fall back to local grep on failure."""
        try:
            results = self._gh_code_search(query, file_pattern)
            return results
        except Exception as exc:
            logger.warning("github_search_api_failed", error=str(exc), falling_back="local_grep")
            return self._local_grep_search(query, file_pattern)

    def _gh_code_search(self, query: str, file_pattern: str) -> list[dict]:
        ext = file_pattern.lstrip("*.")
        search_query = f"{query} repo:{self._repo_name} extension:{ext}"
        results = self._gh.search_code(search_query)
        matches: list[dict] = []
        for item in results[:_MAX_SEARCH_RESULTS]:
            matches.append(
                {
                    "path": item.path,
                    "line": 0,
                    "snippet": f"(GitHub search result — fetch file for context)",
                    "url": item.html_url,
                }
            )
        return matches

    def _local_grep_search(self, query: str, file_pattern: str) -> list[dict]:
        """Grep over cached file contents as fallback."""
        head_sha = ""
        try:
            default_branch = self.repo.default_branch
            head_sha = self.repo.get_branch(default_branch).commit.sha
        except Exception:
            return []

        file_list = self.list_repo_files(head_sha, f"**/{file_pattern}")[:200]
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        matches: list[dict] = []

        for path in file_list:
            if len(matches) >= _MAX_SEARCH_RESULTS:
                break
            content = self.get_file_content(path, head_sha)
            for i, line in enumerate(content.splitlines(), 1):
                if pattern.search(line):
                    matches.append({"path": path, "line": i, "snippet": line.strip()})
                    if len(matches) >= _MAX_SEARCH_RESULTS:
                        break
        return matches

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, retryable_exceptions=(GithubException,))
    def post_inline_comment(
        self,
        pr_id: int | str,
        path: str,
        line: int,
        body: str,
    ) -> None:
        pr: PullRequest = self.repo.get_pull(int(pr_id))
        pr.create_review_comment(
            body=body,
            commit=self.repo.get_commit(pr.head.sha),
            path=path,
            line=line,
            side="RIGHT",
        )
        logger.info("posted_inline_comment", path=path, line=line)

    @retry_with_backoff(max_retries=3, retryable_exceptions=(GithubException,))
    def post_pr_summary(self, pr_id: int | str, body: str) -> None:
        pr: PullRequest = self.repo.get_pull(int(pr_id))
        pr.create_issue_comment(body)
        logger.info("posted_summary_comment", pr_id=pr_id)

    def get_existing_comments(self, pr_id: int | str) -> list[dict]:
        pr: PullRequest = self.repo.get_pull(int(pr_id))
        comments: list[dict] = []

        # Inline review comments
        for c in pr.get_review_comments():
            comments.append(
                {
                    "path": c.path,
                    "line": c.position or c.original_position or 0,
                    "body": c.body,
                    "id": c.id,
                }
            )

        # Top-level issue comments
        for c in pr.get_issue_comments():
            comments.append(
                {"path": "", "line": 0, "body": c.body, "id": c.id}
            )

        return comments

    # ------------------------------------------------------------------
    # Git history
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=2, retryable_exceptions=(GithubException,))
    def get_recent_commits(self, path: str, limit: int = 10) -> list[dict]:
        cache_key = ReviewCache.make_key("gh_commits", self._repo_name, path, str(limit))
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            commits = list(self.repo.get_commits(path=path)[:limit])
        except GithubException:
            return []

        result = [
            {
                "sha": c.sha[:8],
                "message": c.commit.message.splitlines()[0],
                "author": c.commit.author.name if c.commit.author else "unknown",
                "date": str(c.commit.author.date) if c.commit.author else "",
            }
            for c in commits
        ]
        self._cache.set(cache_key, result, ttl=self._cache_ttl)
        return result
