# Operations Quick Reference

**One-page guide for managing enterprise PR review infrastructure**

---

## 🚨 Common Issues & Fixes

### Queue Backlog Growing

**Symptoms:** Queue depth >100 and increasing, reviews delayed >15 min

**Quick fix:**
```bash
# Check current worker count
kubectl get hpa -n pr-reviewer review-worker-hpa

# Manually scale up
kubectl scale deployment review-worker --replicas=30 -n pr-reviewer

# Check queue stats
kubectl exec -n pr-reviewer deploy/webhook-handler -- curl localhost:8080/metrics
```

**Root causes:**
- Traffic spike (normal, HPA will catch up in 1-2 min)
- Workers crashing (check logs)
- API rate limit hit (check circuit breakers)
- Worker nodes maxed out (check node autoscaler)

---

### All Workers Down

**Symptoms:** No pods in `kubectl get pods -n pr-reviewer -l app=review-worker`

**Quick fix:**
```bash
# Check deployment status
kubectl describe deployment review-worker -n pr-reviewer

# Check recent events
kubectl get events -n pr-reviewer --sort-by='.lastTimestamp' | tail -20

# Common causes:
# 1. Image pull error → fix image tag in deployment
# 2. Secrets missing → recreate secrets
# 3. OOM kills → increase memory limits
# 4. Redis connection failure → check Redis pod

# Restart deployment
kubectl rollout restart deployment review-worker -n pr-reviewer
```

---

### GitHub Rate Limit Exceeded

**Symptoms:** Circuit breaker OPEN for `github_api`, queue backs up

**Quick fix:**
```bash
# Check current rate limit
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/rate_limit
# Output: "remaining": 0, "reset": 1710875400

# Temporary: Reduce worker count
kubectl scale deployment review-worker --replicas=5 -n pr-reviewer

# Wait for rate limit reset (shown as Unix timestamp)
date -d @1710875400

# Permanent: Request higher limit from GitHub Enterprise admin
```

**Prevention:**
- Increase cache TTLs (files: 30m → 1h, trees: 1h → 2h)
- Reduce `max_concurrent` in semaphore config
- Enable batch processing (group PRs by repo)

---

### Circuit Breaker Stuck OPEN

**Symptoms:** Reviews blocked, logs show `circuit_breaker_open` for >5 min

**Quick fix:**
```bash
# Check which API is failing
kubectl logs -n pr-reviewer -l app=review-worker | grep circuit_breaker_opened
# Example output: "circuit_breaker_opened" breaker="github_api"

# Verify API is actually healthy
curl -I https://api.github.com  # Should return 200

# Circuit breakers are in-memory, restart workers to reset
kubectl rollout restart deployment review-worker -n pr-reviewer

# Or wait for auto-recovery (60s timeout)
```

**Root cause:** API had transient issues, circuit breaker tripped to protect quota

---

### Redis Connection Failures

**Symptoms:** Workers crash with `ConnectionError: Error connecting to Redis`

**Quick fix:**
```bash
# Check Redis pod
kubectl get pods -n pr-reviewer -l app=redis

# If pod is down, restart
kubectl rollout restart deployment redis -n pr-reviewer

# If using ElastiCache, check AWS console:
aws elasticache describe-cache-clusters \
  --cache-cluster-id pr-reviewer \
  --show-cache-node-info

# Test connection from worker
kubectl exec -n pr-reviewer deploy/review-worker -it -- \
  redis-cli -h redis-service ping
# Expected: PONG
```

---

### High Review Latency (p95 >5 min)

**Symptoms:** Reviews taking >5 min, queue depth normal

**Quick fix:**
```bash
# Check Claude API latency (most common cause)
kubectl logs -n pr-reviewer -l app=review-worker | grep review_duration_seconds
# Look for outliers

# Check cache hit rate
kubectl exec -n pr-reviewer deploy/webhook-handler -- \
  curl localhost:8080/metrics | grep cache_hit_rate
# Should be >0.7

# If cache hit rate low, check Redis memory
kubectl exec -n pr-reviewer redis-0 -- redis-cli INFO memory
# If maxmemory reached, increase Redis size
```

**Root causes:**
- Claude API slow (check Anthropic status page)
- Cache hit rate low (increase TTLs or Redis memory)
- Too many tool calls (reduce `max_tool_calls` from 40 → 30)
- Large PRs (>100 files) → consider splitting

---

## 📊 Monitoring Commands

### Queue Health
```bash
# Queue depth
kubectl exec -n pr-reviewer redis-0 -- redis-cli ZCARD pr_review:queue

# Items by priority
kubectl exec -n pr-reviewer redis-0 -- redis-cli ZRANGE pr_review:queue 0 -1 WITHSCORES

# Oldest item age
kubectl exec -n pr-reviewer redis-0 -- \
  redis-cli ZRANGE pr_review:queue 0 0 WITHSCORES | tail -1
```

### Worker Health
```bash
# Worker count
kubectl get deployment review-worker -n pr-reviewer

# HPA status
kubectl get hpa -n pr-reviewer

# Pod resource usage
kubectl top pods -n pr-reviewer -l app=review-worker

# Recent logs
kubectl logs -n pr-reviewer -l app=review-worker --tail=100 -f
```

### API Rate Limits
```bash
# GitHub
curl -H "Authorization: token $GITHUB_TOKEN" https://api.github.com/rate_limit \
  | jq '.rate'
# Output: {"limit": 5000, "remaining": 3421, "reset": 1710875400}

# Claude (check recent logs)
kubectl logs -n pr-reviewer -l app=review-worker | grep -i "rate limit"
```

### Cache Performance
```bash
# Redis info
kubectl exec -n pr-reviewer redis-0 -- redis-cli INFO stats

# Key count by prefix
kubectl exec -n pr-reviewer redis-0 -- \
  redis-cli KEYS "pr_review_cache:*" | wc -l

# Memory usage
kubectl exec -n pr-reviewer redis-0 -- redis-cli INFO memory
```

---

## 🔧 Configuration Changes

### Scale Workers Manually
```bash
# Temporary override (HPA will revert based on metrics)
kubectl scale deployment review-worker --replicas=20 -n pr-reviewer

# Permanent: edit HPA
kubectl edit hpa review-worker-hpa -n pr-reviewer
# Change minReplicas / maxReplicas
```

### Adjust Rate Limits
Edit semaphore config in queue code:
```python
# src/pr_reviewer/queue/priority_queue.py
def acquire_github_permit(self, timeout: float = 60.0) -> bool:
    return self._acquire_semaphore("github", max_concurrent=20, timeout=timeout)
    # Increase from 10 → 20 for GitHub Enterprise higher tier
```

Redeploy:
```bash
docker build -t your-registry/pr-reviewer:v2.1 .
docker push your-registry/pr-reviewer:v2.1
kubectl set image deployment/review-worker worker=your-registry/pr-reviewer:v2.1 -n pr-reviewer
```

### Adjust Cache TTLs
Edit cache config:
```python
# src/pr_reviewer/cache/shared_cache.py
self._ttls = {
    "file_content": 3600,      # 1 hour (was 30 min)
    "repo_tree": 7200,         # 2 hours (was 1 hour)
}
```

Redeploy (same as above).

### Change Review Config
Via environment variables (no code change needed):
```bash
kubectl set env deployment/review-worker \
  PR_REVIEWER_MAX_TOOL_CALLS=30 \
  PR_REVIEWER_MIN_CONFIDENCE=0.85 \
  -n pr-reviewer
```

---

## 🛠️ Maintenance Tasks

### Clear Queue (Emergency)
```bash
# WARNING: Deletes all pending reviews
kubectl exec -n pr-reviewer redis-0 -- redis-cli DEL pr_review:queue

# Or via webhook API
curl -X DELETE http://webhook-handler/queue/clear
```

### Clear Cache
```bash
# Clear all cached data (force fresh API calls)
kubectl exec -n pr-reviewer redis-0 -- \
  redis-cli --scan --pattern "pr_review_cache:*" | \
  xargs kubectl exec -n pr-reviewer redis-0 -- redis-cli DEL
```

### Drain Node for Maintenance
```bash
# Graceful eviction (workers will finish current review)
kubectl drain NODE_NAME --ignore-daemonsets --delete-emptydir-data

# Workers have 30s shutdown timeout (see WORKER_SHUTDOWN_TIMEOUT)
# In-flight reviews will complete or be requeued
```

### Update Secrets
```bash
# Rotate API keys
kubectl create secret generic pr-reviewer-secrets \
  --from-literal=ANTHROPIC_API_KEY="new-key" \
  --from-literal=GITHUB_TOKEN="new-token" \
  --dry-run=client -o yaml | kubectl apply -f -

# Restart workers to pick up new secrets
kubectl rollout restart deployment review-worker -n pr-reviewer
kubectl rollout restart deployment webhook-handler -n pr-reviewer
```

---

## 📈 Capacity Planning

### Current Capacity
```bash
# Workers
kubectl get deployment review-worker -n pr-reviewer -o jsonpath='{.status.replicas}'

# Throughput per worker: 30-60 PRs/hour
# Total capacity = workers × 40 PRs/hr (average)

# Example: 10 workers = 400 PRs/hr sustained
```

### Scaling Math
```
Target throughput: 1000 PRs/hr
Review duration: 90s avg
Reviews per worker per hour: 3600s / 90s = 40

Required workers: 1000 / 40 = 25
Add 20% buffer: 25 × 1.2 = 30

HPA config:
  minReplicas: 10  (baseline cost optimization)
  maxReplicas: 50  (spike handling)
```

### Node Sizing
```
Worker pod: 250m CPU, 512Mi memory (requests)
Node: m5.large = 2000m CPU, 8192Mi memory (usable)

Pods per node: min(2000/250, 8192/512) = min(8, 16) = 8
BUT: Reserve 20% for system pods → 6 workers/node

30 workers needed → 30/6 = 5 nodes minimum
HPA maxReplicas=50 → 50/6 = 9 nodes maximum

Node autoscaler config:
  minSize: 5
  maxSize: 10
```

---

## 🔍 Debugging Workflow

### Review Not Posted
```bash
# 1. Check if webhook received
kubectl logs -n pr-reviewer -l app=webhook-handler | grep "PR_URL"

# 2. Check if enqueued
kubectl exec -n pr-reviewer redis-0 -- \
  redis-cli ZRANGE pr_review:queue 0 -1 | grep "PR_NUMBER"

# 3. Check if dequeued by worker
kubectl logs -n pr-reviewer -l app=review-worker | grep "review_start.*PR_NUMBER"

# 4. Check if review completed
kubectl logs -n pr-reviewer -l app=review-worker | grep "review_complete.*PR_NUMBER"

# 5. Check for errors
kubectl logs -n pr-reviewer -l app=review-worker | grep "review_failed.*PR_NUMBER"
```

### Worker Pod Crash Loop
```bash
# Get recent pod logs (including previous crashes)
kubectl logs -n pr-reviewer POD_NAME --previous

# Common causes:
# - OOM: Increase memory limits
# - Redis connection: Check REDIS_URL env var
# - Missing secrets: Check secret exists and is mounted
# - Import errors: Check Docker image build

# Describe pod for events
kubectl describe pod POD_NAME -n pr-reviewer
```

---

## 📞 Escalation

**P1 (Critical):**
- All workers down >5 min
- Queue backlog >1000
- Circuit breakers OPEN >10 min

**Contact:** PagerDuty on-call rotation

**P2 (High):**
- Queue backlog >500
- High error rate (>10%)
- API rate limit exhausted

**Contact:** Slack #pr-reviewer-support

**P3 (Normal):**
- Cache hit rate low
- Review latency high
- Questions about config

**Contact:** GitHub Issues or Slack

---

**Last updated:** 2025-03-21
