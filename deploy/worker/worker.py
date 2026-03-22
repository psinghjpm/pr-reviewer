"""Review worker process — dequeues and processes review requests.

Designed for high-volume deployments:
- Pulls from priority queue (Redis)
- Respects rate limits (GitHub, Bitbucket, Claude API)
- Uses shared cache (Redis) to minimize API calls
- Implements circuit breaker for API failures
- Graceful shutdown on SIGTERM (K8s pod eviction)
"""

from __future__ import annotations

import os
import signal
import sys
import time
from typing import Any

import structlog

from pr_reviewer.agent.reviewer import PRReviewer
from pr_reviewer.cache.shared_cache import SharedReviewCache
from pr_reviewer.config import load_config
from pr_reviewer.models import Platform, RepoContext
from pr_reviewer.output.poster import CommentPoster
from pr_reviewer.platforms.base import PlatformAdapter
from pr_reviewer.platforms.bitbucket_adapter import BitbucketAdapter
from pr_reviewer.platforms.github_adapter import GitHubAdapter
from pr_reviewer.queue.priority_queue import PriorityReviewQueue, ReviewRequest
from pr_reviewer.utils.circuit_breaker import CircuitBreaker

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Config from environment
# ---------------------------------------------------------------------------
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
REDIS_CACHE_URL = os.environ.get("REDIS_CACHE_URL", "redis://localhost:6379/1")
WORKER_ID = os.environ.get("HOSTNAME", "worker-unknown")  # K8s pod name
WORKER_CONCURRENCY = int(os.environ.get("WORKER_CONCURRENCY", "1"))
SHUTDOWN_TIMEOUT = int(os.environ.get("WORKER_SHUTDOWN_TIMEOUT", "30"))

# Global shutdown flag
_shutdown_requested = False


def _signal_handler(signum: int, frame: Any) -> None:
    """Handle SIGTERM/SIGINT gracefully."""
    global _shutdown_requested
    logger.info("shutdown_signal_received", signal=signum, worker=WORKER_ID)
    _shutdown_requested = True


signal.signal(signal.SIGTERM, _signal_handler)
signal.signal(signal.SIGINT, _signal_handler)


# ---------------------------------------------------------------------------
# Worker main loop
# ---------------------------------------------------------------------------


class ReviewWorker:
    """Single-threaded review worker that processes queue entries."""

    def __init__(
        self,
        queue: PriorityReviewQueue,
        cache: SharedReviewCache,
        worker_id: str = WORKER_ID,
    ) -> None:
        self._queue = queue
        self._cache = cache
        self._worker_id = worker_id
        self._cfg = load_config()

        # Circuit breakers (prevent cascading failures)
        self._github_breaker = CircuitBreaker(
            failure_threshold=5, recovery_timeout=60.0, name="github_api"
        )
        self._claude_breaker = CircuitBreaker(
            failure_threshold=3, recovery_timeout=30.0, name="claude_api"
        )
        self._bitbucket_breaker = CircuitBreaker(
            failure_threshold=5, recovery_timeout=60.0, name="bitbucket_api"
        )

        # Metrics
        self._reviews_processed = 0
        self._reviews_failed = 0
        self._reviews_skipped = 0

    def run(self) -> None:
        """Main worker loop — dequeue and process reviews until shutdown."""
        logger.info("worker_started", worker_id=self._worker_id)

        while not _shutdown_requested:
            try:
                request = self._queue.dequeue(timeout=5.0)
                if request is None:
                    continue  # Timeout, check shutdown flag

                self._process_request(request)

            except Exception as exc:
                logger.exception("worker_loop_error", error=str(exc))
                time.sleep(1.0)  # Back off on errors

        logger.info(
            "worker_shutdown",
            worker_id=self._worker_id,
            processed=self._reviews_processed,
            failed=self._reviews_failed,
            skipped=self._reviews_skipped,
        )

    def _process_request(self, request: ReviewRequest) -> None:
        """Process a single review request."""
        logger.info(
            "review_start",
            worker=self._worker_id,
            pr_url=request.pr_url,
            priority=request.priority.name,
        )

        start_time = time.time()

        try:
            # Detect platform
            if "github.com" in request.pr_url:
                platform = Platform.GITHUB
                breaker = self._github_breaker
                acquire_permit = self._queue.acquire_github_permit
                release_permit = self._queue.release_github_permit
            elif "bitbucket.org" in request.pr_url or "atlassian" in request.pr_url:
                platform = Platform.BITBUCKET
                breaker = self._bitbucket_breaker
                acquire_permit = self._queue.acquire_bitbucket_permit
                release_permit = self._queue.release_bitbucket_permit
            else:
                logger.error("unsupported_platform", pr_url=request.pr_url)
                self._reviews_skipped += 1
                return

            # Check circuit breaker
            if breaker.is_open():
                logger.warning(
                    "circuit_breaker_open",
                    breaker=breaker.name,
                    pr_url=request.pr_url,
                )
                # Re-queue with lower priority
                self._requeue_with_backoff(request)
                self._reviews_skipped += 1
                return

            # Acquire platform API permit (distributed rate limiting)
            if not acquire_permit(timeout=60.0):
                logger.warning("rate_limit_timeout", platform=platform.value)
                self._requeue_with_backoff(request)
                self._reviews_skipped += 1
                return

            try:
                # Acquire Claude API permit
                if not self._queue.acquire_claude_permit(timeout=60.0):
                    logger.warning("claude_rate_limit_timeout")
                    self._requeue_with_backoff(request)
                    self._reviews_skipped += 1
                    return

                try:
                    # Execute review
                    self._execute_review(request, platform)
                    self._reviews_processed += 1

                    # Mark circuit breaker success
                    breaker.record_success()

                    elapsed = time.time() - start_time
                    logger.info(
                        "review_complete",
                        pr_url=request.pr_url,
                        duration_seconds=elapsed,
                    )

                finally:
                    self._queue.release_claude_permit()

            finally:
                release_permit()

        except Exception as exc:
            self._reviews_failed += 1
            logger.exception("review_failed", pr_url=request.pr_url, error=str(exc))

            # Record circuit breaker failure
            if platform == Platform.GITHUB:
                self._github_breaker.record_failure()
            elif platform == Platform.BITBUCKET:
                self._bitbucket_breaker.record_failure()

    def _execute_review(self, request: ReviewRequest, platform: Platform) -> None:
        """Execute the actual review."""
        # Create platform adapter with shared cache
        adapter = self._create_adapter(request.repo, platform)

        # Load repo context (from cache if available)
        repo_context = self._load_repo_context(request.repo)

        # Create reviewer
        reviewer = PRReviewer(
            adapter=adapter,
            api_key=self._cfg.anthropic.api_key,
            model=self._cfg.anthropic.model,
            max_tool_calls=self._cfg.anthropic.max_tool_calls,
            max_content_length=self._cfg.review.max_content_length,
            repo_context=repo_context,
            temperature=self._cfg.anthropic.temperature,
        )

        # Run review
        session = reviewer.review(pr_id=request.pr_number)

        # Post comments
        poster = CommentPoster(
            adapter=adapter,
            min_severity=self._cfg.review.min_severity_to_post,
            max_inline_comments=self._cfg.review.max_inline_comments,
            dry_run=False,
            min_confidence=self._cfg.review.min_confidence_to_post,
        )

        poster.post(session)

    def _create_adapter(
        self, repo: str, platform: Platform
    ) -> PlatformAdapter:
        """Create platform adapter with shared cache integration."""
        if platform == Platform.GITHUB:
            token = os.environ.get("GITHUB_TOKEN", "")
            # TODO: Integrate shared cache into GitHubAdapter
            return GitHubAdapter(
                token=token,
                repo_full_name=repo,
                cache_ttl=self._cfg.review.cache_ttl_seconds,
            )
        elif platform == Platform.BITBUCKET:
            username = os.environ.get("BITBUCKET_USERNAME", "")
            password = os.environ.get("BITBUCKET_APP_PASSWORD", "")
            workspace, repo_slug = repo.split("/", 1)
            return BitbucketAdapter(
                username=username,
                app_password=password,
                workspace=workspace,
                repo_slug=repo_slug,
                cache_ttl=self._cfg.review.cache_ttl_seconds,
            )
        else:
            raise ValueError(f"Unsupported platform: {platform}")

    def _load_repo_context(self, repo: str) -> RepoContext | None:
        """Load repo context from cache or disk."""
        # Try cache first
        cached = self._cache.get_repo_context(repo)
        if cached:
            return RepoContext(**cached)

        # TODO: Load from disk (.pr-reviewer/repo_context.json)
        return None

    def _requeue_with_backoff(self, request: ReviewRequest) -> None:
        """Re-queue a request with exponential backoff."""
        # Add delay metadata
        backoff_count = request.metadata.get("backoff_count", 0) if request.metadata else 0
        backoff_count += 1

        # Max 3 retries
        if backoff_count > 3:
            logger.warning("max_retries_exceeded", pr_url=request.pr_url)
            return

        # Exponential backoff: 5s, 10s, 20s
        delay = 5 * (2 ** (backoff_count - 1))
        time.sleep(delay)

        # Re-enqueue with updated metadata
        request.metadata = request.metadata or {}
        request.metadata["backoff_count"] = backoff_count
        request.requested_at = time.time()  # Update timestamp

        self._queue.enqueue(request)
        logger.info(
            "request_requeued",
            pr_url=request.pr_url,
            backoff_count=backoff_count,
            delay=delay,
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    """Worker entry point."""
    logger.info(
        "worker_init",
        worker_id=WORKER_ID,
        concurrency=WORKER_CONCURRENCY,
        redis_url=REDIS_URL,
    )

    queue = PriorityReviewQueue(redis_url=REDIS_URL)
    cache = SharedReviewCache(redis_url=REDIS_CACHE_URL)

    worker = ReviewWorker(queue=queue, cache=cache, worker_id=WORKER_ID)

    try:
        worker.run()
        return 0
    except Exception as exc:
        logger.exception("worker_fatal_error", error=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
