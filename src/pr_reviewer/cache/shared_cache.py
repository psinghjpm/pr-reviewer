"""Redis-backed shared cache for PR review data.

Dramatically reduces GitHub/Bitbucket API calls by sharing:
- File contents across multiple reviews
- Repository trees
- Git history
- PR metadata

Cache hierarchy:
1. L1 (in-memory dict) — fast, per-worker
2. L2 (Redis) — shared across all workers
3. L3 (source API) — GitHub/Bitbucket
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

import redis
import structlog

logger = structlog.get_logger(__name__)


class SharedReviewCache:
    """Redis-backed distributed cache for PR review data.

    Key features:
    - Shared across all K8s pods/workers
    - Configurable TTL per cache type
    - Compression for large values (file contents)
    - L1 in-memory cache for hot data
    - Cache warming (pre-fetch common files)
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/1",
        cache_prefix: str = "pr_review_cache",
        default_ttl: int = 300,
        enable_compression: bool = True,
    ) -> None:
        self._redis = redis.from_url(redis_url)
        self._prefix = cache_prefix
        self._default_ttl = default_ttl
        self._enable_compression = enable_compression

        # L1 in-memory cache
        self._l1: dict[str, tuple[Any, float]] = {}
        self._l1_max_size = 1000

        # TTL overrides by cache type
        self._ttls = {
            "file_content": 1800,      # 30 min (files change rarely)
            "repo_tree": 3600,         # 1 hour (tree structure stable)
            "git_history": 7200,       # 2 hours (history immutable)
            "pr_metadata": 300,        # 5 min (metadata changes frequently)
            "pr_diff": 600,            # 10 min (diff changes on push)
            "search_results": 900,     # 15 min (search index updates)
            "repo_context": 86400,     # 24 hours (rarely changes)
        }

    # ------------------------------------------------------------------
    # Core cache operations
    # ------------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        """Get cached value from L1 → L2 → None."""
        # L1 check
        if key in self._l1:
            value, expiry = self._l1[key]
            if time.monotonic() < expiry:
                logger.debug("cache_hit", cache="L1", key=key[:50])
                return value
            del self._l1[key]

        # L2 (Redis) check
        try:
            redis_key = self._make_redis_key(key)
            raw = self._redis.get(redis_key)
            if raw is None:
                logger.debug("cache_miss", key=key[:50])
                return None

            value = self._deserialize(raw)

            # Promote to L1
            self._set_l1(key, value)

            logger.debug("cache_hit", cache="L2", key=key[:50])
            return value

        except Exception as exc:
            logger.warning("cache_get_error", key=key[:50], error=str(exc))
            return None

    def set(
        self,
        key: str,
        value: Any,
        ttl: int | None = None,
        cache_type: str | None = None,
    ) -> None:
        """Store value in L1 + L2."""
        # Determine TTL
        if ttl is None:
            ttl = self._ttls.get(cache_type or "", self._default_ttl)

        # L1
        self._set_l1(key, value, ttl)

        # L2 (Redis)
        try:
            redis_key = self._make_redis_key(key)
            serialized = self._serialize(value)
            self._redis.setex(redis_key, ttl, serialized)
            logger.debug("cache_set", key=key[:50], ttl=ttl)
        except Exception as exc:
            logger.warning("cache_set_error", key=key[:50], error=str(exc))

    def delete(self, key: str) -> None:
        """Remove from both L1 and L2."""
        self._l1.pop(key, None)
        try:
            redis_key = self._make_redis_key(key)
            self._redis.delete(redis_key)
        except Exception:
            pass

    def clear_l1(self) -> None:
        """Clear L1 in-memory cache (called between reviews)."""
        self._l1.clear()

    # ------------------------------------------------------------------
    # High-level cache helpers (domain-specific)
    # ------------------------------------------------------------------

    def get_file_content(
        self, repo: str, ref: str, path: str
    ) -> str | None:
        """Get cached file content."""
        key = f"file:{repo}:{ref}:{path}"
        return self.get(key)

    def set_file_content(
        self, repo: str, ref: str, path: str, content: str
    ) -> None:
        """Cache file content."""
        key = f"file:{repo}:{ref}:{path}"
        self.set(key, content, cache_type="file_content")

    def get_repo_tree(
        self, repo: str, ref: str, pattern: str = "**/*"
    ) -> list[str] | None:
        """Get cached repository file tree."""
        key = f"tree:{repo}:{ref}:{pattern}"
        return self.get(key)

    def set_repo_tree(
        self, repo: str, ref: str, pattern: str, paths: list[str]
    ) -> None:
        """Cache repository file tree."""
        key = f"tree:{repo}:{ref}:{pattern}"
        self.set(key, paths, cache_type="repo_tree")

    def get_pr_metadata(self, repo: str, pr_id: int) -> dict | None:
        """Get cached PR metadata."""
        key = f"pr_meta:{repo}:{pr_id}"
        return self.get(key)

    def set_pr_metadata(self, repo: str, pr_id: int, metadata: dict) -> None:
        """Cache PR metadata."""
        key = f"pr_meta:{repo}:{pr_id}"
        self.set(key, metadata, cache_type="pr_metadata")

    def get_pr_diff(self, repo: str, pr_id: int) -> list | None:
        """Get cached PR diff."""
        key = f"pr_diff:{repo}:{pr_id}"
        return self.get(key)

    def set_pr_diff(self, repo: str, pr_id: int, diff: list) -> None:
        """Cache PR diff."""
        key = f"pr_diff:{repo}:{pr_id}"
        self.set(key, diff, cache_type="pr_diff")

    def get_git_history(
        self, repo: str, path: str, limit: int
    ) -> list[dict] | None:
        """Get cached git commit history."""
        key = f"history:{repo}:{path}:{limit}"
        return self.get(key)

    def set_git_history(
        self, repo: str, path: str, limit: int, commits: list[dict]
    ) -> None:
        """Cache git commit history."""
        key = f"history:{repo}:{path}:{limit}"
        self.set(key, commits, cache_type="git_history")

    def get_repo_context(self, repo: str) -> dict | None:
        """Get cached repo context (conventions, architecture, etc.)."""
        key = f"repo_ctx:{repo}"
        return self.get(key)

    def set_repo_context(self, repo: str, context: dict) -> None:
        """Cache repo context."""
        key = f"repo_ctx:{repo}"
        self.set(key, context, cache_type="repo_context")

    # ------------------------------------------------------------------
    # Batch operations (cache warming)
    # ------------------------------------------------------------------

    def warm_common_files(
        self,
        repo: str,
        ref: str,
        common_patterns: list[str] | None = None,
    ) -> int:
        """Pre-fetch commonly accessed files into cache.

        Returns number of files cached.

        Default patterns:
        - package.json, requirements.txt, go.mod (dependencies)
        - README.md, CONTRIBUTING.md (documentation)
        - .github/workflows/*.yml (CI config)
        """
        if common_patterns is None:
            common_patterns = [
                "package.json",
                "requirements.txt",
                "go.mod",
                "Cargo.toml",
                "pyproject.toml",
                "README.md",
                ".github/workflows/*.yml",
            ]

        # This would be called by the platform adapter
        # (implementation depends on having access to adapter instance)
        logger.info("cache_warm_requested", repo=repo, patterns=common_patterns)
        return 0  # Placeholder

    def mget(self, keys: list[str]) -> dict[str, Any]:
        """Batch get multiple keys."""
        results: dict[str, Any] = {}

        # Check L1 first
        for key in keys:
            value = self.get(key)
            if value is not None:
                results[key] = value

        return results

    def mset(self, items: dict[str, tuple[Any, str | None]]) -> None:
        """Batch set multiple keys.

        items: {key: (value, cache_type)}
        """
        for key, (value, cache_type) in items.items():
            self.set(key, value, cache_type=cache_type)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _make_redis_key(self, key: str) -> str:
        """Create namespaced Redis key."""
        # Hash long keys to avoid Redis key length limits
        if len(key) > 200:
            hashed = hashlib.sha256(key.encode()).hexdigest()[:16]
            return f"{self._prefix}:{hashed}"
        return f"{self._prefix}:{key}"

    def _serialize(self, value: Any) -> bytes:
        """Serialize value for Redis storage."""
        if isinstance(value, str):
            data = value.encode("utf-8")
        else:
            data = json.dumps(value).encode("utf-8")

        # Compress if enabled and size > 1KB
        if self._enable_compression and len(data) > 1024:
            try:
                import zlib
                compressed = zlib.compress(data, level=6)
                # Only use compressed if it's actually smaller
                if len(compressed) < len(data):
                    return b"Z:" + compressed
            except Exception:
                pass

        return data

    def _deserialize(self, data: bytes) -> Any:
        """Deserialize value from Redis."""
        # Check for compression marker
        if data.startswith(b"Z:"):
            try:
                import zlib
                data = zlib.decompress(data[2:])
            except Exception:
                pass

        try:
            # Try JSON first
            return json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            # Fall back to plain string
            return data.decode("utf-8", errors="replace")

    def _set_l1(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store in L1 in-memory cache with LRU eviction."""
        if ttl is None:
            ttl = self._default_ttl

        expiry = time.monotonic() + ttl
        self._l1[key] = (value, expiry)

        # LRU eviction if over max size
        if len(self._l1) > self._l1_max_size:
            # Remove 10% oldest entries
            to_remove = sorted(self._l1.items(), key=lambda x: x[1][1])[
                : self._l1_max_size // 10
            ]
            for k, _ in to_remove:
                del self._l1[k]

    # ------------------------------------------------------------------
    # Monitoring / observability
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return cache statistics for monitoring."""
        try:
            # Redis info
            info = self._redis.info("stats")
            keyspace = self._redis.info("keyspace")

            # L1 stats
            l1_size = len(self._l1)
            l1_expired = sum(
                1 for _, (_, expiry) in self._l1.items() if time.monotonic() >= expiry
            )

            # Redis key count (approximate — count keys with our prefix)
            pattern = f"{self._prefix}:*"
            l2_size = self._redis.dbsize()  # Total keys in DB

            return {
                "l1_size": l1_size,
                "l1_expired": l1_expired,
                "l2_total_keys": l2_size,
                "redis_hits": info.get("keyspace_hits", 0),
                "redis_misses": info.get("keyspace_misses", 0),
                "hit_rate": self._compute_hit_rate(info),
            }
        except Exception as exc:
            logger.warning("cache_stats_error", error=str(exc))
            return {"error": str(exc)}

    def _compute_hit_rate(self, info: dict) -> float:
        """Compute Redis cache hit rate."""
        hits = info.get("keyspace_hits", 0)
        misses = info.get("keyspace_misses", 0)
        total = hits + misses
        return hits / total if total > 0 else 0.0

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    def __enter__(self) -> SharedReviewCache:
        return self

    def __exit__(self, *_: Any) -> None:
        self.clear_l1()
