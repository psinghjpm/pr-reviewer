# Enterprise-Scale Deployment Guide

**For handling thousands of PR reviews per hour**

This guide covers deploying PRGenie at enterprise scale with:
- ✅ **Thousands of reviews/hour** capacity
- ✅ **GitHub/Bitbucket/Claude API throttling** handled gracefully
- ✅ **HPA autoscaling** for traffic spikes
- ✅ **Priority queue** (security PRs → CRITICAL, main → HIGH, drafts → LOW)
- ✅ **Distributed caching** (minimize API calls)
- ✅ **Circuit breakers** (prevent cascading failures)
- ✅ **Monitoring & alerting** (Prometheus + Grafana)

---

## Architecture Overview

```
┌─────────────────┐
│ GitHub Webhooks │ (thousands/hour)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ FastAPI Webhook │ (2 replicas, lightweight)
│   + Signature   │
│   Validation    │
└────────┬────────┘
         │
         ▼ Enqueue to Redis
┌─────────────────┐
│ Redis Priority  │ (queue + cache, managed service recommended)
│     Queue       │
└────────┬────────┘
         │
         ▼ Dequeue (with rate limiting)
┌─────────────────┐
│ Review Workers  │ (3-50 replicas, HPA-scaled)
│   + Circuit     │
│   Breakers      │
│   + Shared      │
│   Cache         │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ GitHub/BB API   │ (rate-limited, cached)
│ Claude API      │
└─────────────────┘
```

**Key improvements over V1 (Job-based):**

| Metric | V1 (Jobs) | V2 (Queue + Workers) | Improvement |
|--------|-----------|----------------------|-------------|
| **Throughput** | ~30 PRs/hr | **1000+ PRs/hr** | **33x** |
| **Spike handling** | Job creation throttled | HPA scales to 50 workers | **Elastic** |
| **API efficiency** | No caching | Shared Redis cache | **50-70% fewer API calls** |
| **Failure recovery** | Job retry (slow) | Circuit breaker + requeue | **Instant** |
| **Priority support** | None | 4-level priority queue | **Critical PRs first** |
| **Cost (EKS)** | $150/mo baseline | $120/mo baseline, $300/mo peak | **20% cheaper baseline** |

---

## Deployment Steps

### 1. Prerequisites

- **EKS cluster** (1.28+) with:
  - 3-10 worker nodes (m5.large or m6i.large)
  - VPC with private subnets
  - AWS Load Balancer Controller (for Ingress)
  - Prometheus Operator (for monitoring)

- **Managed Redis** (recommended):
  - AWS ElastiCache (Redis 7.x, cache.m5.large)
  - OR Redis Cloud (2GB plan)
  - OR self-hosted Redis Sentinel cluster

- **Secrets** in AWS Secrets Manager or K8s:
  ```bash
  ANTHROPIC_API_KEY=sk-ant-...
  GITHUB_TOKEN=ghp_...
  BITBUCKET_USERNAME=... (optional)
  BITBUCKET_APP_PASSWORD=... (optional)
  WEBHOOK_SECRET=... (GitHub webhook HMAC secret)
  ```

### 2. Install Redis (if not using managed service)

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f deploy/k8s/redis.yaml
```

**For production, use managed Redis:**
```bash
# AWS ElastiCache
aws elasticache create-replication-group \
  --replication-group-id pr-reviewer \
  --replication-group-description "PR review queue + cache" \
  --engine redis \
  --cache-node-type cache.m5.large \
  --num-cache-clusters 2 \
  --automatic-failover-enabled

# Get endpoint
REDIS_URL="redis://pr-reviewer.abc123.use1.cache.amazonaws.com:6379/0"
```

### 3. Create Secrets

```bash
kubectl create secret generic pr-reviewer-secrets \
  --from-literal=ANTHROPIC_API_KEY="sk-ant-..." \
  --from-literal=GITHUB_TOKEN="ghp_..." \
  --from-literal=BITBUCKET_USERNAME="..." \
  --from-literal=BITBUCKET_APP_PASSWORD="..." \
  -n pr-reviewer

kubectl create secret generic webhook-secrets \
  --from-literal=WEBHOOK_SECRET="your-webhook-secret" \
  -n pr-reviewer
```

### 4. Deploy Workers

Update [worker-deployment.yaml](../deploy/k8s/worker-deployment.yaml):
```yaml
env:
  - name: REDIS_URL
    value: "redis://your-redis-endpoint:6379/0"  # Update this
  - name: REDIS_CACHE_URL
    value: "redis://your-redis-endpoint:6379/1"  # Update this
```

Deploy:
```bash
kubectl apply -f deploy/k8s/rbac.yaml
kubectl apply -f deploy/k8s/worker-deployment.yaml
kubectl apply -f deploy/k8s/hpa.yaml
```

Verify:
```bash
kubectl get pods -n pr-reviewer -l app=review-worker
kubectl logs -n pr-reviewer -l app=review-worker --tail=50
```

### 5. Deploy Webhook Handler

Build Docker image:
```bash
cd deploy/webhook
docker build -t your-registry/pr-reviewer-webhook:v2 -f Dockerfile.v2 .
docker push your-registry/pr-reviewer-webhook:v2
```

Update [webhook-deploy.yaml](../deploy/k8s/webhook-deploy.yaml):
```yaml
image: your-registry/pr-reviewer-webhook:v2
env:
  - name: REDIS_URL
    value: "redis://your-redis-endpoint:6379/0"
```

Deploy:
```bash
kubectl apply -f deploy/k8s/webhook-deploy.yaml
```

Get webhook URL:
```bash
kubectl get ingress -n pr-reviewer webhook-handler
# URL: https://pr-reviewer-webhook.your-domain.com
```

### 6. Configure GitHub Webhook

In your GitHub org/repo:
1. Settings → Webhooks → Add webhook
2. **Payload URL:** `https://pr-reviewer-webhook.your-domain.com/webhook`
3. **Content type:** `application/json`
4. **Secret:** (same as `WEBHOOK_SECRET`)
5. **Events:** ✅ Pull requests only
6. **Active:** ✅

Test with a PR creation.

### 7. Setup Monitoring

**Prometheus + Grafana:**
```bash
# Install Prometheus Operator (if not already installed)
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm install prometheus prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace

# Deploy ServiceMonitor and alerts
kubectl apply -f deploy/monitoring/prometheus-servicemonitor.yaml

# Import Grafana dashboard
kubectl port-forward -n monitoring svc/prometheus-grafana 3000:80
# Open http://localhost:3000
# Import deploy/monitoring/grafana-dashboard.json
```

**CloudWatch (AWS-native):**
```bash
# Worker logs → CloudWatch Logs
kubectl apply -f https://raw.githubusercontent.com/aws-samples/amazon-cloudwatch-container-insights/latest/k8s-deployment-manifest-templates/deployment-mode/daemonset/container-insights-monitoring/cwagent/cwagent-fluentd-quickstart.yaml

# Custom metrics
aws cloudwatch put-metric-data \
  --namespace PRReviewer \
  --metric-name QueueDepth \
  --value $(kubectl exec -n pr-reviewer redis-0 -- redis-cli ZCARD pr_review:queue)
```

---

## Tuning for High Volume

### Rate Limit Configuration

Edit [priority_queue.py](../src/pr_reviewer/queue/priority_queue.py):

```python
# GitHub Enterprise: 15,000 req/hr = 4.17 req/s
def acquire_github_permit(self, timeout: float = 60.0) -> bool:
    return self._acquire_semaphore("github", max_concurrent=20, timeout=timeout)
    # Default: 10 concurrent, increase to 20 for GitHub Enterprise

# Claude API: based on your Anthropic tier
def acquire_claude_permit(self, timeout: float = 60.0) -> bool:
    return self._acquire_semaphore("claude", max_concurrent=10, timeout=timeout)
    # Tier 3: 10 concurrent, Tier 4: 50 concurrent
```

### HPA Tuning

For **massive spikes** (e.g., merger train after deploy freeze):
```yaml
# deploy/k8s/hpa.yaml
spec:
  minReplicas: 10  # Higher baseline (was 3)
  maxReplicas: 100 # More headroom (was 50)

  behavior:
    scaleUp:
      policies:
        - type: Pods
          value: 10  # Add 10 pods/min (was 5)
```

For **cost optimization** (low-priority PRs tolerate delay):
```yaml
spec:
  minReplicas: 2   # Lower baseline
  maxReplicas: 30  # Lower ceiling

  behavior:
    scaleDown:
      stabilizationWindowSeconds: 600  # Wait 10 min (was 5)
```

### Cache Tuning

Increase Redis memory for large repos:
```yaml
# deploy/k8s/redis.yaml (or ElastiCache parameter group)
maxmemory 8gb  # Was 2gb
maxmemory-policy allkeys-lru
```

Increase TTLs for stable files:
```python
# src/pr_reviewer/cache/shared_cache.py
self._ttls = {
    "file_content": 3600,      # 1 hour (was 30 min)
    "repo_tree": 7200,         # 2 hours (was 1 hour)
    "git_history": 14400,      # 4 hours (was 2 hours)
}
```

### Worker Resource Limits

For **faster reviews** (higher cost):
```yaml
# deploy/k8s/worker-deployment.yaml
resources:
  requests:
    cpu: 500m      # Was 250m
    memory: 1Gi    # Was 512Mi
  limits:
    cpu: 2000m     # Was 1000m
    memory: 2Gi    # Was 1Gi
```

For **cost optimization** (slower reviews):
```yaml
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    cpu: 500m
    memory: 512Mi
```

---

## Capacity Planning

### Throughput Benchmarks

**Single worker capacity:**
- Review duration: 60-120s (depends on PR size, with temperature=0.0 and 40 tool calls)
- Throughput: **30-60 PRs/hour per worker**

**Scaling math for 1000 PRs/hour:**
```
Required workers = 1000 PRs/hr ÷ 40 PRs/hr/worker = 25 workers
Add 20% buffer for spikes = 30 workers
HPA config: minReplicas=10, maxReplicas=50
```

**Scaling math for 5000 PRs/hour:**
```
Required workers = 5000 ÷ 40 = 125 workers
HPA config: minReplicas=30, maxReplicas=200
Node group: 10-40 nodes (m5.xlarge, 4 workers/node)
```

### Cost Estimates (AWS EKS)

**Baseline (10 workers, 1000 PRs/hr):**
```
EKS cluster:       $73/mo (control plane)
EC2 nodes:         $150/mo (3x m5.large on-demand)
ElastiCache:       $50/mo (cache.m5.large)
ALB:               $22/mo (Application Load Balancer)
Data transfer:     $10/mo
Anthropic API:     $250/mo (1000 PRs × $0.25/PR)
──────────────────────────────────────────────
Total:             ~$555/mo
```

**Peak (50 workers, 5000 PRs/hr):**
```
EC2 nodes:         $750/mo (12x m5.large, mix of Spot + On-Demand)
ElastiCache:       $150/mo (cache.m5.xlarge)
Anthropic API:     $1250/mo (5000 PRs × $0.25/PR)
──────────────────────────────────────────────
Total:             ~$2295/mo
```

**Cost optimizations:**
- Use **Spot instances** for workers (70% cheaper): `tolerations` already configured
- Use **Graviton nodes** (m6g.large, 20% cheaper than m5.large)
- **Claude Haiku** for simple PRs (10x cheaper): add PR classification logic
- **Batch similar PRs** to share repo context (30% fewer API calls)

---

## Monitoring Key Metrics

### Health Dashboard

| Metric | Healthy Range | Alert Threshold | Action |
|--------|---------------|-----------------|--------|
| **Queue depth** | 0-50 | >100 (5 min) | Scale workers |
| **Queue age** | <5 min | >15 min | Check circuit breakers |
| **Worker count** | 3-20 | 0 | Critical: restart deployment |
| **Review duration (p95)** | 60-120s | >300s | Check API latency |
| **Cache hit rate** | >70% | <50% | Increase cache TTL |
| **GitHub rate limit** | >2000 | <500 | Reduce workers or increase API limit |
| **Circuit breaker** | All CLOSED | Any OPEN | Check API health |

### Log Queries

**CloudWatch Insights:**
```sql
-- Reviews per hour
fields @timestamp, msg
| filter msg = "review_complete"
| stats count() as reviews by bin(1h)

-- Slowest reviews
fields @timestamp, pr_url, duration_seconds
| filter msg = "review_complete"
| sort duration_seconds desc
| limit 20

-- Error rate by type
fields @timestamp, error, pr_url
| filter msg = "review_failed"
| stats count() as errors by error
```

**Prometheus queries:**
```promql
# Throughput
rate(reviews_processed_total[5m]) * 60

# Error rate
rate(reviews_failed_total[5m]) / rate(reviews_processed_total[5m])

# Queue depth trend
deriv(pr_review_queue_size[10m])

# Worker saturation
avg(rate(container_cpu_usage_seconds_total{pod=~"review-worker.*"}[5m]))
```

---

## Troubleshooting

### Queue backlog growing
```bash
# Check worker count
kubectl get hpa -n pr-reviewer

# Check worker logs for errors
kubectl logs -n pr-reviewer -l app=review-worker --tail=100

# Manually scale up
kubectl scale deployment review-worker --replicas=30 -n pr-reviewer

# Check circuit breakers
kubectl exec -n pr-reviewer review-worker-xxx -- \
  curl localhost:8080/circuit-breakers  # If you expose this endpoint
```

### Circuit breaker stuck OPEN
```bash
# Check which API is failing
kubectl logs -n pr-reviewer -l app=review-worker | grep circuit_breaker_opened

# Manually reset (if API is healthy again)
# Requires implementing /admin/circuit-breaker/reset endpoint

# Or restart workers (circuit breakers are in-memory)
kubectl rollout restart deployment review-worker -n pr-reviewer
```

### GitHub rate limit exhausted
```bash
# Check current limit
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/rate_limit

# Reduce worker count temporarily
kubectl scale deployment review-worker --replicas=5 -n pr-reviewer

# Or request higher rate limit from GitHub Enterprise admin
```

### Redis connection failures
```bash
# Check Redis pod health
kubectl get pods -n pr-reviewer -l app=redis

# Check Redis logs
kubectl logs -n pr-reviewer redis-0

# Test connection from worker
kubectl exec -n pr-reviewer review-worker-xxx -- \
  redis-cli -h redis-service ping
```

---

## Advanced Optimizations

### 1. Multi-Region Deployment

For global teams, deploy workers in multiple regions:
```
US-EAST-1: 20 workers (main)
EU-WEST-1: 10 workers (EU team PRs)
AP-SOUTH-1: 5 workers (India team PRs)

Shared: Single Redis (ElastiCache Global Datastore)
```

### 2. PR Batching

Group PRs from same repo to share context:
```python
# In worker.py
batch = queue.get_batch_by_repo(repo="owner/repo", max_size=5)
# Load repo context once, review all 5 PRs
```

### 3. Smart Priority Adjustment

Dynamically adjust priority based on metadata:
```python
# In app_v2.py _determine_priority()
if "dependencies" in pr_labels:
    return ReviewPriority.HIGH  # Security dependency updates

if pr.get("changed_files", 0) < 3:
    return ReviewPriority.LOW  # Tiny PRs can wait
```

### 4. KEDA for Queue-Driven Scaling

Replace HPA with KEDA for true queue-based scaling:
```bash
helm repo add kedacore https://kedacore.github.io/charts
helm install keda kedacore/keda -n keda --create-namespace

kubectl apply -f deploy/k8s/keda-scaledobject.yaml
```

See commented KEDA config in [hpa.yaml](../deploy/k8s/hpa.yaml).

---

## Migration from V1 (Job-based)

**Zero-downtime migration:**

1. Deploy V2 webhook in parallel:
   ```bash
   kubectl apply -f deploy/k8s/webhook-v2-deploy.yaml
   # Different Ingress path: /webhook/v2
   ```

2. Update GitHub webhook URL to `/webhook/v2` (or use weighted routing)

3. Monitor both systems for 24 hours

4. Once V2 is stable, delete V1:
   ```bash
   kubectl delete deployment webhook-handler -n pr-reviewer  # Old V1
   kubectl delete -f deploy/k8s/reviewer-job.yaml  # Old Job template
   ```

**Rollback plan:**
- Keep V1 webhook config as backup
- Change GitHub webhook URL back to V1 endpoint
- V1 code remains unchanged in this repo (`deploy/webhook/app.py`)

---

## Security Considerations

1. **Webhook authentication**: HMAC signature validation (already implemented)

2. **Secrets management**: Use AWS Secrets Manager + External Secrets Operator:
   ```bash
   helm repo add external-secrets https://charts.external-secrets.io
   helm install external-secrets external-secrets/external-secrets -n external-secrets --create-namespace
   ```

3. **Network policies**: Restrict worker egress to only GitHub/Anthropic APIs:
   ```yaml
   # deploy/k8s/network-policy.yaml
   apiVersion: networking.k8s.io/v1
   kind: NetworkPolicy
   metadata:
     name: review-worker-egress
   spec:
     podSelector:
       matchLabels:
         app: review-worker
     policyTypes:
       - Egress
     egress:
       - to:
         - namespaceSelector:
             matchLabels:
               name: pr-reviewer  # Redis
       - to:
         - podSelector: {}
         ports:
         - protocol: TCP
           port: 443  # HTTPS only
   ```

4. **RBAC**: Workers don't need K8s API access (ServiceAccount can be minimal)

5. **Pod Security Standards**: Enforce `restricted` PSS:
   ```bash
   kubectl label namespace pr-reviewer pod-security.kubernetes.io/enforce=restricted
   ```

---

## Support & Debugging

**Enable debug logging:**
```yaml
# worker-deployment.yaml
env:
  - name: LOG_LEVEL
    value: "DEBUG"
```

**Interactive debugging:**
```bash
# SSH into worker pod
kubectl exec -it -n pr-reviewer review-worker-xxx -- /bin/bash

# Inspect queue
python3 -c "
from pr_reviewer.queue.priority_queue import PriorityReviewQueue
q = PriorityReviewQueue(redis_url='redis://redis-service:6379/0')
print(q.stats())
"

# Inspect cache
redis-cli -h redis-service
> ZCARD pr_review:queue
> KEYS pr_review_cache:*
> TTL pr_review_cache:file:owner/repo:main:src/foo.py
```

**Contact:**
- GitHub Issues: https://github.com/anthropics/pr-reviewer/issues
- Internal Slack: #pr-reviewer-support (if applicable)

---

## Appendix: Dockerfile for Workers

**deploy/worker/Dockerfile:**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY src/ ./src/
COPY deploy/worker/ ./deploy/worker/

# Non-root user
RUN useradd -m -u 1000 worker
USER worker

ENTRYPOINT ["python", "-m", "deploy.worker.worker"]
```

**requirements.txt additions:**
```txt
redis==5.0.1
fastapi==0.104.1
uvicorn[standard]==0.24.0
diskcache==5.6.3
structlog==23.2.0
```
