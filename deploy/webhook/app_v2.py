"""
FastAPI webhook handler — V2 with priority queue.

Improvements over V1:
- Enqueues to Redis instead of spawning K8s Jobs directly
- Priority-based routing (security PRs → CRITICAL, main → HIGH, etc.)
- Deduplication (same PR queued multiple times = single review)
- Rate limit awareness (won't overwhelm GitHub/Claude APIs)
- Monitoring endpoints for queue stats

Architecture:
  GitHub webhook → FastAPI → Redis Queue → Worker Pods

Environment variables:
  WEBHOOK_SECRET        GitHub webhook secret for HMAC validation
  REDIS_URL             Redis connection URL (queue + cache)
"""

import hashlib
import hmac
import logging
import os
import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

# Import priority queue
import sys
sys.path.insert(0, "/app/src")  # Adjust for your Docker image
from pr_reviewer.queue.priority_queue import PriorityReviewQueue, ReviewPriority, ReviewRequest

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="pr-reviewer-webhook-v2")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
WEBHOOK_SECRET: str = os.environ["WEBHOOK_SECRET"]
REDIS_URL: str = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Initialize queue
queue = PriorityReviewQueue(redis_url=REDIS_URL)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_signature(payload: bytes, sig_header: str | None) -> None:
    """Raise HTTP 401 if the GitHub HMAC signature does not match."""
    if not sig_header:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing signature")
    if not sig_header.startswith("sha256="):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unsupported signature scheme")
    expected = "sha256=" + hmac.new(
        WEBHOOK_SECRET.encode(), payload, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, sig_header):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid signature")


def _determine_priority(pr: dict, base_branch: str) -> ReviewPriority:
    """Determine review priority based on PR metadata.

    Priority rules (customize for your org):
    - CRITICAL: PR labels include 'security' or 'hotfix'
    - HIGH: Target = main/master branch, non-draft
    - NORMAL: Feature branches, non-draft
    - LOW: Draft PRs or docs-only changes
    """
    labels = [label["name"].lower() for label in pr.get("labels", [])]

    # CRITICAL: Security or hotfix
    if any(keyword in labels for keyword in ["security", "hotfix", "urgent"]):
        return ReviewPriority.CRITICAL

    # LOW: Draft PRs
    if pr.get("draft", False):
        return ReviewPriority.LOW

    # LOW: Docs-only (all changed files end with .md)
    changed_files = pr.get("changed_files", 0)
    if changed_files > 0:
        # This heuristic requires fetching file list (expensive)
        # For now, rely on labels
        if "documentation" in labels:
            return ReviewPriority.LOW

    # HIGH: Main branch
    if base_branch in ("main", "master", "production", "prod"):
        return ReviewPriority.HIGH

    # NORMAL: Everything else
    return ReviewPriority.NORMAL


def _extract_pr_data(body: dict) -> tuple[str, str, int, dict]:
    """Extract (pr_url, repo, pr_number, pr_metadata) from webhook payload."""
    pr = body.get("pull_request", {})
    pr_url: str = pr.get("html_url", "")
    pr_number: int = pr.get("number", 0)
    repo: str = body.get("repository", {}).get("full_name", "unknown")

    if not pr_url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing pull_request.html_url",
        )

    pr_metadata = {
        "title": pr.get("title", ""),
        "author": pr.get("user", {}).get("login", ""),
        "base_branch": pr.get("base", {}).get("ref", ""),
        "head_branch": pr.get("head", {}).get("ref", ""),
        "labels": [label["name"] for label in pr.get("labels", [])],
        "draft": pr.get("draft", False),
        "changed_files": pr.get("changed_files", 0),
    }

    return pr_url, repo, pr_number, pr_metadata


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/healthz")
async def healthz() -> dict:
    """Health check endpoint."""
    return {"status": "ok", "queue_size": queue.size()}


@app.get("/metrics")
async def metrics() -> dict:
    """Queue metrics for monitoring (Prometheus-compatible)."""
    stats = queue.stats()
    return {
        "queue_size": stats["queue_size"],
        "by_priority": stats["by_priority"],
        "oldest_request_age_seconds": stats["oldest_request_age_seconds"],
        "active_semaphores": stats["active_semaphores"],
    }


@app.get("/queue")
async def queue_peek() -> dict:
    """Peek at the next 20 items in the queue (for debugging)."""
    items = queue.peek(count=20)
    return {
        "queue_size": queue.size(),
        "next_items": [
            {
                "pr_url": item.pr_url,
                "priority": item.priority.name,
                "requested_at": item.requested_at,
                "age_seconds": time.time() - item.requested_at,
            }
            for item in items
        ],
    }


@app.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict:
    """GitHub webhook handler — enqueues review requests."""
    payload = await request.body()
    _verify_signature(payload, x_hub_signature_256)

    # Only process pull_request events
    if x_github_event != "pull_request":
        return {"ignored": True, "reason": f"event={x_github_event}"}

    body = await request.json()
    action = body.get("action", "")

    # Only review on: opened, synchronize (new commits), reopened
    if action not in ("opened", "synchronize", "reopened"):
        return {"ignored": True, "reason": f"action={action}"}

    # Extract PR data
    pr_url, repo, pr_number, pr_metadata = _extract_pr_data(body)
    base_branch = pr_metadata["base_branch"]

    # Determine priority
    pr = body.get("pull_request", {})
    priority = _determine_priority(pr, base_branch)

    # Create review request
    review_request = ReviewRequest(
        pr_url=pr_url,
        repo=repo,
        pr_number=pr_number,
        priority=priority,
        requested_at=time.time(),
        requester="github_webhook",
        metadata=pr_metadata,
    )

    # Enqueue (deduplication handled inside)
    enqueued = queue.enqueue(review_request)

    log.info(
        "webhook_processed",
        action=action,
        pr_url=pr_url,
        priority=priority.name,
        enqueued=enqueued,
        queue_size=queue.size(),
    )

    return {
        "accepted": True,
        "pr_url": pr_url,
        "priority": priority.name,
        "enqueued": enqueued,
        "queue_size": queue.size(),
    }


@app.post("/enqueue", status_code=status.HTTP_202_ACCEPTED)
async def manual_enqueue(
    pr_url: str,
    priority: str = "NORMAL",
) -> dict:
    """Manual enqueue endpoint (for testing or manual triggers).

    Example:
      curl -X POST "http://webhook/enqueue?pr_url=https://github.com/owner/repo/pull/42&priority=HIGH"
    """
    try:
        priority_enum = ReviewPriority[priority.upper()]
    except KeyError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid priority: {priority}. Must be one of {[p.name for p in ReviewPriority]}",
        )

    # Parse repo and PR number from URL
    # Format: https://github.com/owner/repo/pull/123
    parts = pr_url.rstrip("/").split("/")
    if len(parts) < 2 or "pull" not in parts:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid PR URL format",
        )

    pr_number = int(parts[-1])
    repo = "/".join(parts[-4:-2])  # owner/repo

    request = ReviewRequest(
        pr_url=pr_url,
        repo=repo,
        pr_number=pr_number,
        priority=priority_enum,
        requested_at=time.time(),
        requester="manual_api",
    )

    enqueued = queue.enqueue(request)

    return {
        "accepted": True,
        "pr_url": pr_url,
        "priority": priority_enum.name,
        "enqueued": enqueued,
        "queue_size": queue.size(),
    }


@app.delete("/queue/clear")
async def clear_queue() -> dict:
    """Clear all items from the queue (admin operation)."""
    removed = queue.clear()
    log.warning("queue_cleared", removed=removed)
    return {"cleared": removed}


# ---------------------------------------------------------------------------
# Startup / shutdown
# ---------------------------------------------------------------------------


@app.on_event("startup")
async def startup() -> None:
    log.info(
        "webhook_v2_started",
        redis_url=REDIS_URL,
        queue_size=queue.size(),
    )


@app.on_event("shutdown")
async def shutdown() -> None:
    log.info("webhook_v2_shutdown")
