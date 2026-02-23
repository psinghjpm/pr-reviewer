"""Bitbucket platform adapter using atlassian-python-api + raw requests."""

from __future__ import annotations

import fnmatch
import re
from typing import Any

import requests
import structlog
from atlassian import Bitbucket

from pr_reviewer.models import FileDiff, Platform, PRMetadata
from pr_reviewer.platforms.base import PlatformAdapter
from pr_reviewer.utils.cache import ReviewCache
from pr_reviewer.utils.diff_parser import parse_diff
from pr_reviewer.utils.rate_limiter import bitbucket_rate_limited, retry_with_backoff

logger = structlog.get_logger(__name__)

_BB_API = "https://api.bitbucket.org/2.0"
_MAX_SEARCH_RESULTS = 50
_MAX_GREP_FILES = 200


class BitbucketAdapter(PlatformAdapter):
    """Pull request adapter for Bitbucket Cloud repositories."""

    def __init__(
        self,
        username: str,
        app_password: str,
        workspace: str,
        repo_slug: str,
        cache: ReviewCache | None = None,
        cache_ttl: int = 300,
    ) -> None:
        self._username = username
        self._app_password = app_password
        self._workspace = workspace
        self._repo_slug = repo_slug
        self._cache = cache or ReviewCache()
        self._cache_ttl = cache_ttl

        self._bb = Bitbucket(
            url="https://api.bitbucket.org",
            username=username,
            password=app_password,
            cloud=True,
        )
        self._session = requests.Session()
        self._session.auth = (username, app_password)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _api(self, path: str, **params: Any) -> dict:
        url = f"{_BB_API}/{path}"
        resp = self._session.get(url, params=params, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _api_post(self, path: str, json: Any) -> dict:
        url = f"{_BB_API}/{path}"
        resp = self._session.post(url, json=json, timeout=30)
        resp.raise_for_status()
        return resp.json()

    def _pr_path(self, pr_id: int | str) -> str:
        return f"repositories/{self._workspace}/{self._repo_slug}/pullrequests/{pr_id}"

    # ------------------------------------------------------------------
    # PR data
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, retryable_exceptions=(requests.RequestException,))
    def get_pr_metadata(self, pr_id: int | str) -> PRMetadata:
        pr = self._api(self._pr_path(pr_id))
        return PRMetadata(
            pr_id=pr["id"],
            title=pr["title"],
            description=pr.get("description", ""),
            author=pr["author"]["display_name"],
            source_branch=pr["source"]["branch"]["name"],
            target_branch=pr["destination"]["branch"]["name"],
            base_sha=pr["destination"]["commit"]["hash"],
            head_sha=pr["source"]["commit"]["hash"],
            platform=Platform.BITBUCKET,
            repo_full_name=f"{self._workspace}/{self._repo_slug}",
            url=pr["links"]["html"]["href"],
            is_draft=pr.get("draft", False),
        )

    @retry_with_backoff(max_retries=3, retryable_exceptions=(requests.RequestException,))
    def get_pr_diff(self, pr_id: int | str) -> list[FileDiff]:
        url = f"{_BB_API}/{self._pr_path(pr_id)}/diff"
        resp = self._session.get(url, timeout=30)
        resp.raise_for_status()
        return parse_diff(resp.text)

    # ------------------------------------------------------------------
    # File / repo access
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, retryable_exceptions=(requests.RequestException,))
    def get_file_content(self, path: str, ref: str) -> str:
        cache_key = ReviewCache.make_key("bb_file", self._workspace, self._repo_slug, ref, path)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        url = (
            f"{_BB_API}/repositories/{self._workspace}/{self._repo_slug}"
            f"/src/{ref}/{path}"
        )
        resp = self._session.get(url, timeout=30)
        if resp.status_code == 404:
            return ""
        resp.raise_for_status()
        text = resp.text
        self._cache.set(cache_key, text, ttl=self._cache_ttl)
        return text

    @retry_with_backoff(max_retries=2, retryable_exceptions=(requests.RequestException,))
    def list_repo_files(self, ref: str, pattern: str = "**/*.py") -> list[str]:
        cache_key = ReviewCache.make_key("bb_tree", self._workspace, self._repo_slug, ref, pattern)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        paths: list[str] = []
        url = (
            f"{_BB_API}/repositories/{self._workspace}/{self._repo_slug}"
            f"/src/{ref}/"
        )
        self._walk_tree(url, paths, pattern)
        self._cache.set(cache_key, paths, ttl=self._cache_ttl * 6)
        return paths

    def _walk_tree(self, url: str, paths: list[str], pattern: str) -> None:
        """Recursively walk Bitbucket /src tree to collect file paths."""
        while url:
            resp = self._session.get(url, params={"pagelen": 100}, timeout=30)
            if resp.status_code != 200:
                return
            data = resp.json()
            for entry in data.get("values", []):
                if entry["type"] == "commit_file":
                    path = entry["path"]
                    if fnmatch.fnmatch(path, pattern):
                        paths.append(path)
                elif entry["type"] == "commit_directory":
                    suburl = entry["links"]["self"]["href"]
                    self._walk_tree(suburl, paths, pattern)
            url = data.get("next", "")

    def search_repo_code(self, query: str, file_pattern: str = "*.py") -> list[dict]:
        """Bitbucket has no code search API — use local grep over cached files."""
        # Get HEAD sha
        try:
            main_ref = self._api(
                f"repositories/{self._workspace}/{self._repo_slug}/refs/branches/main"
            )
            head_sha = main_ref["target"]["hash"]
        except Exception:
            try:
                main_ref = self._api(
                    f"repositories/{self._workspace}/{self._repo_slug}/refs/branches/master"
                )
                head_sha = main_ref["target"]["hash"]
            except Exception:
                return []

        file_list = self.list_repo_files(head_sha, f"**/{file_pattern}")[:_MAX_GREP_FILES]
        pattern_re = re.compile(re.escape(query), re.IGNORECASE)
        matches: list[dict] = []

        for path in file_list:
            if len(matches) >= _MAX_SEARCH_RESULTS:
                break
            content = self.get_file_content(path, head_sha)
            for i, line in enumerate(content.splitlines(), 1):
                if pattern_re.search(line):
                    matches.append({"path": path, "line": i, "snippet": line.strip()})
                    if len(matches) >= _MAX_SEARCH_RESULTS:
                        break

        return matches

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, retryable_exceptions=(requests.RequestException,))
    def post_inline_comment(
        self,
        pr_id: int | str,
        path: str,
        line: int,
        body: str,
    ) -> None:
        payload = {
            "content": {"raw": body},
            "inline": {"to": line, "path": path},
        }
        self._api_post(f"{self._pr_path(pr_id)}/comments", json=payload)
        logger.info("posted_inline_comment", path=path, line=line)

    @retry_with_backoff(max_retries=3, retryable_exceptions=(requests.RequestException,))
    def post_pr_summary(self, pr_id: int | str, body: str) -> None:
        payload = {"content": {"raw": body}}
        self._api_post(f"{self._pr_path(pr_id)}/comments", json=payload)
        logger.info("posted_summary_comment", pr_id=pr_id)

    def get_existing_comments(self, pr_id: int | str) -> list[dict]:
        comments: list[dict] = []
        url = f"{_BB_API}/{self._pr_path(pr_id)}/comments"
        while url:
            resp = self._session.get(url, params={"pagelen": 50}, timeout=30)
            if resp.status_code != 200:
                break
            data = resp.json()
            for c in data.get("values", []):
                inline = c.get("inline", {})
                comments.append(
                    {
                        "path": inline.get("path", ""),
                        "line": inline.get("to", 0),
                        "body": c.get("content", {}).get("raw", ""),
                        "id": c.get("id"),
                    }
                )
            url = data.get("next", "")
        return comments

    # ------------------------------------------------------------------
    # Git history
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=2, retryable_exceptions=(requests.RequestException,))
    def get_recent_commits(self, path: str, limit: int = 10) -> list[dict]:
        cache_key = ReviewCache.make_key(
            "bb_commits", self._workspace, self._repo_slug, path, str(limit)
        )
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # type: ignore[return-value]

        try:
            data = self._api(
                f"repositories/{self._workspace}/{self._repo_slug}/commits",
                path=path,
                pagelen=limit,
            )
        except Exception:
            return []

        result = [
            {
                "sha": c["hash"][:8],
                "message": c.get("message", "").splitlines()[0],
                "author": c.get("author", {}).get("raw", "unknown"),
                "date": c.get("date", ""),
            }
            for c in data.get("values", [])[:limit]
        ]
        self._cache.set(cache_key, result, ttl=self._cache_ttl)
        return result
