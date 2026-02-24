# EKS Deployment Guide

Event-driven PR reviews on EKS. GitHub sends a webhook → FastAPI handler → K8s Job → `pr-reviewer review --url`.

## Architecture

```
GitHub PR opened/updated
  → GitHub Webhook (POST /webhook)
    → ALB/Ingress → webhook-handler Deployment (FastAPI, 2 replicas)
      → creates K8s Job via kubernetes Python client
        → Job pod: pr-reviewer review --url $PR_URL
          → Anthropic API + GitHub API
            → inline comments + summary posted to the PR
```

## Prerequisites

- EKS cluster (or any Kubernetes cluster)
- `kubectl` configured to point at your cluster
- Docker + ECR (or any container registry)
- `aws` CLI (for ECR push)

---

## 1. Build & Push Images

### Reviewer image (runs `pr-reviewer review`)

```bash
# From repo root
docker build -t pr-reviewer:latest .

# Tag and push to ECR (substitute your values)
ACCOUNT=123456789012
REGION=us-east-1
REPO=pr-reviewer

aws ecr create-repository --repository-name $REPO --region $REGION 2>/dev/null || true
aws ecr get-login-password --region $REGION | \
  docker login --username AWS --password-stdin $ACCOUNT.dkr.ecr.$REGION.amazonaws.com

docker tag pr-reviewer:latest $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest
docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$REPO:latest
```

### Webhook handler image

```bash
WEBHOOK_REPO=pr-reviewer-webhook

aws ecr create-repository --repository-name $WEBHOOK_REPO --region $REGION 2>/dev/null || true

docker build -t $WEBHOOK_REPO:latest deploy/webhook/
docker tag $WEBHOOK_REPO:latest $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$WEBHOOK_REPO:latest
docker push $ACCOUNT.dkr.ecr.$REGION.amazonaws.com/$WEBHOOK_REPO:latest
```

---

## 2. Configure Secrets

Edit `deploy/k8s/secrets.yaml` and replace all `REPLACE_ME` values:

```yaml
# pr-reviewer-secrets
ANTHROPIC_API_KEY: "sk-ant-..."
GITHUB_TOKEN: "ghp_..."

# webhook-secrets
WEBHOOK_SECRET: "a-random-secret-you-will-configure-in-github"
```

> **Do not commit real values.** Consider using [External Secrets Operator](https://external-secrets.io/)
> or [Sealed Secrets](https://github.com/bitnami-labs/sealed-secrets) for production.

---

## 3. Update Image References

Edit `deploy/k8s/webhook-deploy.yaml` and replace the two `REPLACE_WITH_YOUR_*_IMAGE` placeholders
with the ECR URIs you pushed above.

Edit `deploy/k8s/job-template.yaml` similarly.

---

## 4. Apply Manifests

Apply in dependency order:

```bash
# 1. Namespace first
kubectl apply -f deploy/k8s/namespace.yaml

# 2. Secrets
kubectl apply -f deploy/k8s/secrets.yaml

# 3. RBAC (ServiceAccount + Role + RoleBinding)
kubectl apply -f deploy/k8s/rbac.yaml

# 4. Webhook Deployment + Service
kubectl apply -f deploy/k8s/webhook-deploy.yaml

# Verify pods are Running
kubectl get pods -n pr-reviewer
```

Or apply everything at once (order is handled by K8s):

```bash
kubectl apply -f deploy/k8s/
```

---

## 5. Expose the Webhook Endpoint

### Option A: AWS ALB Ingress (recommended for production)

Uncomment the Ingress block at the bottom of `deploy/k8s/webhook-deploy.yaml`, fill in:
- `host`: your domain (e.g. `pr-reviewer-webhook.example.com`)
- `certificate-arn`: your ACM certificate ARN

Then apply:

```bash
kubectl apply -f deploy/k8s/webhook-deploy.yaml
kubectl get ingress -n pr-reviewer  # wait for ADDRESS
```

### Option B: kubectl port-forward (local testing only)

```bash
kubectl port-forward svc/webhook-handler 8080:8080 -n pr-reviewer
```

---

## 6. Configure GitHub Webhook

1. Go to your GitHub repo → **Settings → Webhooks → Add webhook**
2. **Payload URL**: `https://pr-reviewer-webhook.example.com/webhook`
3. **Content type**: `application/json`
4. **Secret**: the value you put in `WEBHOOK_SECRET`
5. **Events**: select **"Let me select individual events"** → tick **Pull requests**
6. Click **Add webhook**

---

## 7. Verify

### Health check

```bash
curl https://pr-reviewer-webhook.example.com/healthz
# {"status":"ok"}
```

### Send a test webhook payload

```bash
# Create a minimal test payload
cat > /tmp/test-payload.json <<'EOF'
{
  "action": "opened",
  "number": 1,
  "pull_request": {
    "html_url": "https://github.com/owner/repo/pull/1",
    "number": 1
  },
  "repository": {
    "full_name": "owner/repo"
  }
}
EOF

# Compute HMAC signature (replace YOUR_SECRET)
SIG=$(echo -n "$(cat /tmp/test-payload.json)" | \
  openssl dgst -sha256 -hmac "YOUR_SECRET" | awk '{print "sha256="$2}')

curl -s -X POST https://pr-reviewer-webhook.example.com/webhook \
  -H "Content-Type: application/json" \
  -H "X-GitHub-Event: pull_request" \
  -H "X-Hub-Signature-256: $SIG" \
  -d @/tmp/test-payload.json
```

### Check Jobs

```bash
kubectl get jobs -n pr-reviewer
kubectl logs job/<job-name> -n pr-reviewer
```

---

## 8. Run a One-Off Review Manually

```bash
export PR_URL="https://github.com/owner/repo/pull/42"
envsubst < deploy/k8s/job-template.yaml | kubectl apply -f -
kubectl logs -f job/pr-review-manual -n pr-reviewer
```

---

## Cleanup

```bash
# Delete completed jobs older than 1 h (TTL handles this automatically)
kubectl delete jobs --field-selector=status.successful=1 -n pr-reviewer

# Remove everything
kubectl delete namespace pr-reviewer
```
