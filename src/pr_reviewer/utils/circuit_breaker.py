"""Circuit breaker pattern for API fault tolerance.

Prevents cascading failures when external APIs (GitHub, Bitbucket, Claude)
are experiencing issues. Automatically opens circuit after N consecutive
failures, then tries to recover after a timeout.

States:
- CLOSED: Normal operation, all requests allowed
- OPEN: Circuit tripped, all requests fail fast
- HALF_OPEN: Testing recovery, limited requests allowed
"""

from __future__ import annotations

import threading
import time
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class CircuitState(Enum):
    """Circuit breaker states."""
    CLOSED = "closed"       # Normal operation
    OPEN = "open"           # Failing, block requests
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """Thread-safe circuit breaker for external API calls.

    Args:
        failure_threshold: Number of consecutive failures before opening
        recovery_timeout: Seconds to wait before attempting recovery
        success_threshold: Successes needed in HALF_OPEN to close circuit
        name: Identifier for logging
    """

    def __init__(
        self,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        success_threshold: int = 2,
        name: str = "circuit",
    ) -> None:
        self.name = name
        self._failure_threshold = failure_threshold
        self._recovery_timeout = recovery_timeout
        self._success_threshold = success_threshold

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = 0.0

        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def is_open(self) -> bool:
        """Return True if circuit is OPEN (requests should fail fast)."""
        with self._lock:
            self._check_recovery()
            return self._state == CircuitState.OPEN

    def record_success(self) -> None:
        """Record a successful API call."""
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                logger.info(
                    "circuit_breaker_success",
                    name=self.name,
                    state=self._state.value,
                    successes=self._success_count,
                    threshold=self._success_threshold,
                )

                if self._success_count >= self._success_threshold:
                    self._close_circuit()
            elif self._state == CircuitState.CLOSED:
                # Reset failure count on success
                if self._failure_count > 0:
                    logger.debug(
                        "circuit_breaker_failures_reset",
                        name=self.name,
                        previous_failures=self._failure_count,
                    )
                    self._failure_count = 0

    def record_failure(self) -> None:
        """Record a failed API call."""
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()

            logger.warning(
                "circuit_breaker_failure",
                name=self.name,
                state=self._state.value,
                failures=self._failure_count,
                threshold=self._failure_threshold,
            )

            if (
                self._state == CircuitState.CLOSED
                and self._failure_count >= self._failure_threshold
            ):
                self._open_circuit()
            elif self._state == CircuitState.HALF_OPEN:
                # Any failure in HALF_OPEN immediately reopens circuit
                self._open_circuit()

    def reset(self) -> None:
        """Manually reset circuit to CLOSED state."""
        with self._lock:
            logger.info("circuit_breaker_reset", name=self.name)
            self._close_circuit()

    def get_state(self) -> CircuitState:
        """Return current circuit state."""
        with self._lock:
            self._check_recovery()
            return self._state

    def stats(self) -> dict:
        """Return circuit breaker statistics."""
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
                "failure_threshold": self._failure_threshold,
                "recovery_timeout": self._recovery_timeout,
                "last_failure_time": self._last_failure_time,
            }

    # ------------------------------------------------------------------
    # Internal state transitions
    # ------------------------------------------------------------------

    def _open_circuit(self) -> None:
        """Transition to OPEN state (circuit tripped)."""
        self._state = CircuitState.OPEN
        self._success_count = 0
        logger.error(
            "circuit_breaker_opened",
            name=self.name,
            failures=self._failure_count,
            recovery_in=self._recovery_timeout,
        )

    def _close_circuit(self) -> None:
        """Transition to CLOSED state (normal operation)."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        logger.info("circuit_breaker_closed", name=self.name)

    def _half_open_circuit(self) -> None:
        """Transition to HALF_OPEN state (testing recovery)."""
        self._state = CircuitState.HALF_OPEN
        self._success_count = 0
        self._failure_count = 0
        logger.info("circuit_breaker_half_open", name=self.name)

    def _check_recovery(self) -> None:
        """Check if recovery timeout has elapsed (OPEN → HALF_OPEN)."""
        if self._state == CircuitState.OPEN:
            elapsed = time.time() - self._last_failure_time
            if elapsed >= self._recovery_timeout:
                self._half_open_circuit()


# ---------------------------------------------------------------------------
# Decorator for automatic circuit breaker integration
# ---------------------------------------------------------------------------


def with_circuit_breaker(breaker: CircuitBreaker):
    """Decorator that wraps a function with circuit breaker logic.

    Example:
        github_breaker = CircuitBreaker(name="github_api")

        @with_circuit_breaker(github_breaker)
        def fetch_pr_data(pr_id: int):
            return github_api.get_pull_request(pr_id)
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            if breaker.is_open():
                raise CircuitBreakerOpenError(
                    f"Circuit breaker '{breaker.name}' is OPEN"
                )

            try:
                result = func(*args, **kwargs)
                breaker.record_success()
                return result
            except Exception as exc:
                breaker.record_failure()
                raise

        return wrapper

    return decorator


class CircuitBreakerOpenError(Exception):
    """Raised when circuit breaker is OPEN and request is rejected."""
    pass
