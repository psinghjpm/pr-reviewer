"""DiskCache wrapper with L1 in-session dict and L2 persistent disk cache."""

from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any

import diskcache


class ReviewCache:
    """Two-level cache.

    - L1: in-process dict (cleared between reviews).
    - L2: diskcache on disk (persists between CI steps / CLI invocations).
    """

    def __init__(self, directory: str = ".pr_reviewer_cache", ttl: int = 300) -> None:
        self._ttl = ttl
        self._l1: dict[str, tuple[Any, float]] = {}  # (value, expiry_timestamp)
        self._l2: diskcache.Cache = diskcache.Cache(directory)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(self, key: str) -> Any | None:
        """Return cached value or None on cache miss / expiry."""
        # L1 check
        if key in self._l1:
            value, expiry = self._l1[key]
            if time.monotonic() < expiry:
                return value
            del self._l1[key]

        # L2 check
        try:
            value = self._l2.get(key)
        except Exception:
            return None

        if value is not None:
            # Promote to L1
            self._l1[key] = (value, time.monotonic() + self._ttl)
        return value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store a value in both L1 and L2 caches."""
        effective_ttl = ttl if ttl is not None else self._ttl
        expiry = time.monotonic() + effective_ttl
        self._l1[key] = (value, expiry)
        try:
            self._l2.set(key, value, expire=effective_ttl)
        except Exception:
            pass  # Disk issues shouldn't break the review

    def delete(self, key: str) -> None:
        self._l1.pop(key, None)
        try:
            self._l2.delete(key)
        except Exception:
            pass

    def clear_l1(self) -> None:
        """Clear the in-session L1 cache (called between reviews)."""
        self._l1.clear()

    def close(self) -> None:
        try:
            self._l2.close()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def make_key(*parts: str) -> str:
        """Create a deterministic cache key from multiple string parts."""
        raw = ":".join(parts)
        return hashlib.sha256(raw.encode()).hexdigest()

    def cached_call(self, key: str, fn: Any, *args: Any, ttl: int | None = None, **kwargs: Any) -> Any:
        """Return cached result or call fn(*args, **kwargs) and cache the result."""
        result = self.get(key)
        if result is None:
            result = fn(*args, **kwargs)
            self.set(key, result, ttl=ttl)
        return result

    def __enter__(self) -> "ReviewCache":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()
