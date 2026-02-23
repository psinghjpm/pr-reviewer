"""Token-bucket rate limiter for API calls."""

from __future__ import annotations

import threading
import time
from functools import wraps
from typing import Any, Callable, TypeVar

import structlog

logger = structlog.get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])


class TokenBucket:
    """Thread-safe token bucket rate limiter.

    Args:
        rate: Tokens added per second.
        capacity: Maximum tokens in the bucket.
    """

    def __init__(self, rate: float, capacity: float) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: float = 1.0, timeout: float = 60.0) -> bool:
        """Block until `tokens` are available or `timeout` seconds elapse.

        Returns True if tokens were acquired, False on timeout.
        """
        deadline = time.monotonic() + timeout
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return True
            wait = (tokens - self._tokens) / self._rate
            if time.monotonic() + wait > deadline:
                return False
            time.sleep(min(wait, 0.1))

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._last_refill = now


# ---------------------------------------------------------------------------
# Pre-configured buckets (module-level singletons)
# ---------------------------------------------------------------------------

# GitHub REST API: 5000 req/hour = ~1.39 req/s
_github_bucket = TokenBucket(rate=1.39, capacity=10)

# GitHub Code Search API: 30 req/min = 0.5 req/s
_github_search_bucket = TokenBucket(rate=0.5, capacity=5)

# Claude API: conservative limit (Anthropic may impose per-minute limits)
_claude_bucket = TokenBucket(rate=0.5, capacity=3)

# Bitbucket REST API: 1000 req/hour = ~0.28 req/s
_bitbucket_bucket = TokenBucket(rate=0.28, capacity=5)


def rate_limited(bucket: TokenBucket, tokens: float = 1.0) -> Callable[[F], F]:
    """Decorator that acquires tokens from *bucket* before each call."""

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            acquired = bucket.consume(tokens)
            if not acquired:
                raise TimeoutError(f"Rate limit timeout waiting to call {fn.__name__}")
            return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


def github_rate_limited(fn: F) -> F:
    return rate_limited(_github_bucket)(fn)


def github_search_rate_limited(fn: F) -> F:
    return rate_limited(_github_search_bucket)(fn)


def claude_rate_limited(fn: F) -> F:
    return rate_limited(_claude_bucket)(fn)


def bitbucket_rate_limited(fn: F) -> F:
    return rate_limited(_bitbucket_bucket)(fn)


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    backoff_factor: float = 2.0,
    retryable_exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """Decorator: exponential back-off retry on retryable exceptions."""

    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            delay = base_delay
            for attempt in range(max_retries + 1):
                try:
                    return fn(*args, **kwargs)
                except retryable_exceptions as exc:
                    if attempt == max_retries:
                        raise
                    logger.warning(
                        "retrying_after_error",
                        fn=fn.__name__,
                        attempt=attempt + 1,
                        delay=delay,
                        error=str(exc),
                    )
                    time.sleep(delay)
                    delay *= backoff_factor

        return wrapper  # type: ignore[return-value]

    return decorator
