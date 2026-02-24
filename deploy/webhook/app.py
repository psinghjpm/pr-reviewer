"""
FastAPI webhook handler — receives GitHub PR events and spawns K8s Jobs.

Environment variables:
  WEBHOOK_SECRET        GitHub webhook secret for HMAC validation
  REVIEWER_IMAGE        Docker image for the reviewer Job pods
  K8S_NAMESPACE         Namespace to create Jobs in (default: pr-reviewer)
  K8S_JOB_SECRET_NAME   Secret that holds ANTHROPIC_API_KEY / GITHUB_TOKEN
                        (default: pr-reviewer-secrets)
"""

import hashlib
import hmac
import logging
import os
import re
import time

from fastapi import FastAPI, Header, HTTPException, Request, status
from kubernetes import client, config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

app = FastAPI(title="pr-reviewer-webhook")

# ---------------------------------------------------------------------------
# Config (all from env vars so Kubernetes Secrets work cleanly)
# ---------------------------------------------------------------------------
WEBHOOK_SECRET: str = os.environ["WEBHOOK_SECRET"]
REVIEWER_IMAGE: str = os.environ["REVIEWER_IMAGE"]
K8S_NAMESPACE: str = os.environ.get("K8S_NAMESPACE", "pr-reviewer")
K8S_JOB_SECRET_NAME: str = os.environ.get("K8S_JOB_SECRET_NAME", "pr-reviewer-secrets")

# Load in-cluster config (falls back to kubeconfig for local testing)
try:
    config.load_incluster_config()
    log.info("Loaded in-cluster Kubernetes config")
except config.ConfigException:
    config.load_kube_config()
    log.info("Loaded local kubeconfig (development mode)")

batch_v1 = client.BatchV1Api()


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


def _safe_job_name(repo: str, pr_number: int) -> str:
    """Build a DNS-safe Job name like pr-review-owner-repo-42-1708000000."""
    slug = re.sub(r"[^a-z0-9-]", "-", repo.lower())[:40].strip("-")
    ts = int(time.time())
    return f"pr-review-{slug}-{pr_number}-{ts}"


def _create_review_job(pr_url: str, repo: str, pr_number: int) -> str:
    """Create a K8s Job that runs `pr-reviewer review --url <PR_URL>`."""
    job_name = _safe_job_name(repo, pr_number)

    job = client.V1Job(
        api_version="batch/v1",
        kind="Job",
        metadata=client.V1ObjectMeta(
            name=job_name,
            namespace=K8S_NAMESPACE,
            labels={
                "app": "pr-reviewer",
                "pr-number": str(pr_number),
            },
        ),
        spec=client.V1JobSpec(
            restart_policy="Never",
            backoff_limit=1,
            ttl_seconds_after_finished=3600,  # auto-clean after 1 h
            template=client.V1PodTemplateSpec(
                metadata=client.V1ObjectMeta(
                    labels={"app": "pr-reviewer", "pr-number": str(pr_number)}
                ),
                spec=client.V1PodSpec(
                    restart_policy="Never",
                    containers=[
                        client.V1Container(
                            name="reviewer",
                            image=REVIEWER_IMAGE,
                            args=["review", "--url", pr_url],
                            env=[
                                # Pass PR_URL explicitly for observability
                                client.V1EnvVar(name="PR_URL", value=pr_url),
                                # Secrets sourced from K8s Secret
                                client.V1EnvVar(
                                    name="ANTHROPIC_API_KEY",
                                    value_from=client.V1EnvVarSource(
                                        secret_key_ref=client.V1SecretKeySelector(
                                            name=K8S_JOB_SECRET_NAME,
                                            key="ANTHROPIC_API_KEY",
                                        )
                                    ),
                                ),
                                client.V1EnvVar(
                                    name="GITHUB_TOKEN",
                                    value_from=client.V1EnvVarSource(
                                        secret_key_ref=client.V1SecretKeySelector(
                                            name=K8S_JOB_SECRET_NAME,
                                            key="GITHUB_TOKEN",
                                        )
                                    ),
                                ),
                                client.V1EnvVar(
                                    name="PR_REVIEWER_MODEL",
                                    value_from=client.V1EnvVarSource(
                                        secret_key_ref=client.V1SecretKeySelector(
                                            name=K8S_JOB_SECRET_NAME,
                                            key="PR_REVIEWER_MODEL",
                                            optional=True,
                                        )
                                    ),
                                ),
                            ],
                            resources=client.V1ResourceRequirements(
                                requests={"cpu": "100m", "memory": "256Mi"},
                                limits={"cpu": "500m", "memory": "512Mi"},
                            ),
                        )
                    ],
                ),
            ),
        ),
    )

    batch_v1.create_namespaced_job(namespace=K8S_NAMESPACE, body=job)
    log.info("Created job %s for PR %s", job_name, pr_url)
    return job_name


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
async def github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(default=None),
    x_github_event: str | None = Header(default=None),
) -> dict:
    payload = await request.body()
    _verify_signature(payload, x_hub_signature_256)

    if x_github_event != "pull_request":
        return {"ignored": True, "reason": f"event={x_github_event}"}

    body = await request.json()
    action = body.get("action", "")
    if action not in ("opened", "synchronize", "reopened"):
        return {"ignored": True, "reason": f"action={action}"}

    pr = body.get("pull_request", {})
    pr_url: str = pr.get("html_url", "")
    pr_number: int = pr.get("number", 0)
    repo: str = body.get("repository", {}).get("full_name", "unknown")

    if not pr_url:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing pull_request.html_url")

    job_name = _create_review_job(pr_url=pr_url, repo=repo, pr_number=pr_number)
    return {"accepted": True, "job": job_name, "pr_url": pr_url}
