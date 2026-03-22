"""Redis-backed priority queue for PR review requests.

Supports:
- Priority-based ordering (CRITICAL > HIGH > NORMAL > LOW)
- Rate limiting (GitHub, Bitbucket, Claude API)
- Batching (group multiple PRs from same repo)
- Deduplication (same PR queued multiple times = single review)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

import redis
import structlog

logger = structlog.get_logger(__name__)


class ReviewPriority(Enum):
    """Priority levels for review queue."""
    CRITICAL = 4  # Security PRs, hotfixes
    HIGH = 3      # Main branch PRs
    NORMAL = 2    # Feature branch PRs
    LOW = 1       # Draft PRs, docs-only


@dataclass
class ReviewRequest:
    """A single PR review request."""
    pr_url: str
    repo: str
    pr_number: int
    priority: ReviewPriority
    requested_at: float
    requester: str = "webhook"
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict:
        return {
            "pr_url": self.pr_url,
            "repo": self.repo,
            "pr_number": self.pr_number,
            "priority": self.priority.name,
            "requested_at": self.requested_at,
            "requester": self.requester,
            "metadata": self.metadata or {},
        }

    @classmethod
    def from_dict(cls, data: dict) -> ReviewRequest:
        return cls(
            pr_url=data["pr_url"],
            repo=data["repo"],
            pr_number=data["pr_number"],
            priority=ReviewPriority[data["priority"]],
            requested_at=data["requested_at"],
            requester=data.get("requester", "webhook"),
            metadata=data.get("metadata"),
        )


class PriorityReviewQueue:
    """Redis-backed priority queue with deduplication and rate limiting."""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        queue_prefix: str = "pr_review",
    ) -> None:
        self._redis = redis.from_url(redis_url, decode_responses=True)
        self._queue_prefix = queue_prefix
        self._lock_ttl = 300  # 5 min lock expiry

    # ------------------------------------------------------------------
    # Queue operations
    # ------------------------------------------------------------------

    def enqueue(self, request: ReviewRequest) -> bool:
        """Add a review request to the queue.

        Returns True if enqueued, False if already in queue (deduplication).
        """
        dedup_key = self._dedup_key(request.repo, request.pr_number)

        # Check if already queued or in-progress
        if self._redis.exists(dedup_key):
            logger.info(
                "review_already_queued",
                pr_url=request.pr_url,
                dedup_key=dedup_key,
            )
            return False

        # Add to dedup set with 1-hour TTL (covers review + retry window)
        self._redis.setex(dedup_key, 3600, "1")

        # Add to priority queue (sorted set by score)
        # Score = priority * 1e10 + timestamp (higher priority = higher score)
        score = request.priority.value * 1e10 + request.requested_at
        queue_key = self._queue_key()

        self._redis.zadd(queue_key, {json.dumps(request.to_dict()): score})

        logger.info(
            "review_enqueued",
            pr_url=request.pr_url,
            priority=request.priority.name,
            queue_size=self._redis.zcard(queue_key),
        )
        return True

    def dequeue(self, timeout: float = 10.0) -> ReviewRequest | None:
        """Pop the highest-priority request from the queue.

        Blocks for up to `timeout` seconds if queue is empty.
        Returns None on timeout.
        """
        queue_key = self._queue_key()
        deadline = time.monotonic() + timeout

        while time.monotonic() < deadline:
            # Pop highest score (ZREVRANGE = descending order)
            items = self._redis.zrevrange(queue_key, 0, 0)
            if not items:
                time.sleep(0.1)
                continue

            # Atomically remove from queue
            removed = self._redis.zrem(queue_key, items[0])
            if removed == 0:
                continue  # Race condition, already taken

            data = json.loads(items[0])
            request = ReviewRequest.from_dict(data)

            logger.info(
                "review_dequeued",
                pr_url=request.pr_url,
                priority=request.priority.name,
                wait_time=time.monotonic() - request.requested_at,
            )
            return request

        return None

    def peek(self, count: int = 10) -> list[ReviewRequest]:
        """Return the next `count` requests without removing them."""
        queue_key = self._queue_key()
        items = self._redis.zrevrange(queue_key, 0, count - 1)
        return [ReviewRequest.from_dict(json.loads(item)) for item in items]

    def size(self) -> int:
        """Return the number of queued requests."""
        return self._redis.zcard(self._queue_key())

    def clear(self) -> int:
        """Remove all requests from the queue. Returns number removed."""
        return self._redis.delete(self._queue_key())

    # ------------------------------------------------------------------
    # Rate limiting (distributed locks)
    # ------------------------------------------------------------------

    def acquire_github_permit(self, timeout: float = 60.0) -> bool:
        """Acquire a GitHub API permit (distributed semaphore).

        Returns True if acquired, False on timeout.
        """
        return self._acquire_semaphore("github", max_concurrent=10, timeout=timeout)

    def release_github_permit(self) -> None:
        """Release a GitHub API permit."""
        self._release_semaphore("github")

    def acquire_claude_permit(self, timeout: float = 60.0) -> bool:
        """Acquire a Claude API permit (distributed semaphore)."""
        return self._acquire_semaphore("claude", max_concurrent=5, timeout=timeout)

    def release_claude_permit(self) -> None:
        """Release a Claude API permit."""
        self._release_semaphore("claude")

    def acquire_bitbucket_permit(self, timeout: float = 60.0) -> bool:
        """Acquire a Bitbucket API permit (distributed semaphore)."""
        return self._acquire_semaphore("bitbucket", max_concurrent=5, timeout=timeout)

    def release_bitbucket_permit(self) -> None:
        """Release a Bitbucket API permit."""
        self._release_semaphore("bitbucket")

    # ------------------------------------------------------------------
    # Batching helpers
    # ------------------------------------------------------------------

    def get_batch_by_repo(self, repo: str, max_size: int = 5) -> list[ReviewRequest]:
        """Get a batch of requests for the same repository.

        Useful for:
        - Sharing repo context across reviews
        - Amortizing GitHub API calls (fetch file tree once)
        """
        queue_key = self._queue_key()
        all_items = self._redis.zrevrange(queue_key, 0, -1)

        batch: list[ReviewRequest] = []
        for item in all_items:
            request = ReviewRequest.from_dict(json.loads(item))
            if request.repo == repo:
                batch.append(request)
                if len(batch) >= max_size:
                    break

        return batch

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _queue_key(self) -> str:
        return f"{self._queue_prefix}:queue"

    def _dedup_key(self, repo: str, pr_number: int) -> str:
        return f"{self._queue_prefix}:dedup:{repo}:{pr_number}"

    def _semaphore_key(self, name: str) -> str:
        return f"{self._queue_prefix}:semaphore:{name}"

    def _acquire_semaphore(
        self, name: str, max_concurrent: int, timeout: float
    ) -> bool:
        """Distributed counting semaphore using Redis sorted set + TTL."""
        key = self._semaphore_key(name)
        deadline = time.monotonic() + timeout
        token = f"{time.time_ns()}"  # Unique token per acquisition

        while time.monotonic() < deadline:
            now = time.time()

            # Remove expired tokens (TTL = lock_ttl seconds)
            self._redis.zremrangebyscore(key, 0, now - self._lock_ttl)

            # Count active tokens
            active_count = self._redis.zcard(key)

            if active_count < max_concurrent:
                # Try to add our token
                self._redis.zadd(key, {token: now})
                logger.debug(
                    "semaphore_acquired",
                    semaphore=name,
                    active=active_count + 1,
                    max=max_concurrent,
                )
                return True

            time.sleep(0.1)

        logger.warning("semaphore_timeout", semaphore=name, timeout=timeout)
        return False

    def _release_semaphore(self, name: str) -> None:
        """Release one token from the semaphore (removes oldest)."""
        key = self._semaphore_key(name)
        # Remove oldest token (lowest score = earliest timestamp)
        removed = self._redis.zpopmin(key)
        if removed:
            logger.debug("semaphore_released", semaphore=name)

    # ------------------------------------------------------------------
    # Monitoring / observability
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """Return queue statistics for monitoring."""
        queue_key = self._queue_key()
        total_size = self._redis.zcard(queue_key)

        # Count by priority
        by_priority: dict[str, int] = {p.name: 0 for p in ReviewPriority}
        all_items = self._redis.zrevrange(queue_key, 0, -1)
        for item in all_items:
            request = ReviewRequest.from_dict(json.loads(item))
            by_priority[request.priority.name] += 1

        # Oldest request age
        oldest_age = 0.0
        if all_items:
            oldest = ReviewRequest.from_dict(json.loads(all_items[-1]))
            oldest_age = time.time() - oldest.requested_at

        # Semaphore counts
        semaphores = {
            "github": self._redis.zcard(self._semaphore_key("github")),
            "claude": self._redis.zcard(self._semaphore_key("claude")),
            "bitbucket": self._redis.zcard(self._semaphore_key("bitbucket")),
        }

        return {
            "queue_size": total_size,
            "by_priority": by_priority,
            "oldest_request_age_seconds": oldest_age,
            "active_semaphores": semaphores,
        }
