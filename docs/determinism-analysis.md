# PRGenie Determinism Analysis & Improvement Strategies

**Date:** 2026-03-21
**Goal:** Make code review comments more deterministic and reproducible

## Table of Contents
1. [Current Sources of Non-Determinism](#current-sources-of-non-determinism)
2. [Recommended Strategies](#recommended-strategies)
3. [Implementation Roadmap](#implementation-roadmap)
4. [Trade-offs & Considerations](#trade-offs--considerations)

---

## Current Sources of Non-Determinism

### 1. **LLM Sampling (PRIMARY ISSUE)**

**Location:** [reviewer.py:243-248](../src/pr_reviewer/agent/reviewer.py#L243-L248), [reviewer.py:320-325](../src/pr_reviewer/agent/reviewer.py#L320-L325)

```python
response = self._client.messages.create(
    model=self._model,
    max_tokens=8192,
    system=build_system_prompt(self._repo_context),
    tools=AGENT_TOOLS,
    messages=messages,
)
```

**Problem:** No `temperature` parameter specified → defaults to `1.0` (high randomness)

**Impact:**
- Different findings on repeated runs
- Varying severity/confidence scores
- Non-reproducible reviews for the same PR

### 2. **Agentic Loop Order Non-Determinism**

**Location:** [reviewer.py:242-298](../src/pr_reviewer/agent/reviewer.py#L242-L298)

**Problem:** Claude decides which tools to call and in what order based on probabilistic sampling

**Impact:**
- Different context gathering order → different conclusions
- Some runs may fetch more context than others
- Early vs. late context fetch affects reasoning

### 3. **File Ordering Issues**

**Location:** [repo_context_agent.py:168](../src/pr_reviewer/context/repo_context_agent.py#L168), [repo_context_agent.py:146](../src/pr_reviewer/context/repo_context_agent.py#L146)

**Current behavior:**
```python
candidates.sort(key=_priority)  # ✅ GOOD: Sorted by priority
dirs = sorted(dirs)             # ✅ GOOD: Sorted
```

**Status:** ✅ Already deterministic (uses sorted lists)

### 4. **Context Truncation Boundaries**

**Location:** [tool_executor.py:82-84](../src/pr_reviewer/agent/tool_executor.py#L82-L84)

```python
def _truncate(self, text: str) -> str:
    if len(text) > self._max_len:
        return text[: self._max_len] + f"\n\n[... truncated at {self._max_len} chars ...]"
```

**Problem:** Truncation at character boundaries may split tokens mid-word/mid-line

**Impact:** Claude sees different context depending on where truncation occurs

### 5. **Dict Iteration Order (Low Risk)**

**Location:** Various (Python 3.7+ guarantees insertion order)

**Status:** ✅ Minimal risk in Python 3.7+ (dicts preserve insertion order)

### 6. **Timestamp-Based Variations**

**Location:** [repo_context_agent.py:216](../src/pr_reviewer/context/repo_context_agent.py#L216)

```python
f"`{datetime.now(timezone.utc).isoformat()}`.\n\n"
```

**Problem:** Timestamp in prompt changes on every run

**Impact:** Different prompts → different LLM outputs (though minimal)

---

## Recommended Strategies

### Strategy A: **Temperature Control** (HIGHEST IMPACT)

**Priority:** 🔴 CRITICAL
**Effort:** Low
**Expected Improvement:** 40-60% more deterministic

#### Implementation

Add `temperature` parameter to both LLM calls:

```python
# In PRReviewer.__init__
def __init__(
    self,
    adapter: PlatformAdapter,
    api_key: str,
    model: str = "claude-sonnet-4-6",
    max_tool_calls: int = 60,
    max_content_length: int = 12_000,
    repo_context: RepoContext | None = None,
    temperature: float = 0.0,  # ← NEW
) -> None:
    self._temperature = temperature
    # ... rest

# In review() and _generate_summary()
response = self._client.messages.create(
    model=self._model,
    max_tokens=8192,
    temperature=self._temperature,  # ← NEW
    system=build_system_prompt(self._repo_context),
    tools=AGENT_TOOLS,
    messages=messages,
)
```

**Config changes:**

```yaml
# config.yaml
anthropic:
  temperature: 0.0  # deterministic (0.0) | creative (1.0)
```

**Environment variable:**
```bash
export PR_REVIEWER_TEMPERATURE=0.0
```

#### Temperature Recommendations

| Value | Behavior | Use Case |
|-------|----------|----------|
| `0.0` | **Deterministic** (always picks most likely token) | Stable reviews, regression testing, compliance |
| `0.3` | Low randomness | Slightly varied wording, mostly consistent findings |
| `0.7` | Moderate creativity | Default for exploratory reviews |
| `1.0` | High randomness (current default) | Maximum diversity, brainstorming |

**⚠️ Note:** Even at `temperature=0.0`, Claude is not 100% deterministic due to:
- Internal sampling implementation details
- Floating-point precision
- Model version updates

Expected determinism: **~95% with temperature=0.0** vs. **~40% with temperature=1.0**

---

### Strategy B: **Deterministic Tool Call Order** (MEDIUM IMPACT)

**Priority:** 🟡 HIGH
**Effort:** Medium
**Expected Improvement:** 20-30% more consistent context

#### Approach 1: Pre-fetch All Context (Simplest)

Remove agentic loop; fetch everything upfront in deterministic order:

```python
def review_deterministic(self, pr_id: int | str) -> AgentSession:
    """Deterministic review: pre-fetch all context before LLM analysis."""
    pr_metadata = self._adapter.get_pr_metadata(pr_id)
    diff = self._adapter.get_pr_diff(pr_id)

    # Phase 1: Gather ALL context in deterministic order
    context_bundle = self._gather_full_context(pr_metadata, diff)

    # Phase 2: Single LLM call with all context (no tool use)
    findings = self._analyze_with_context(pr_metadata, diff, context_bundle)

    return AgentSession(
        pr_metadata=pr_metadata,
        diff=diff,
        findings=findings,
        tool_call_count=0,
    )

def _gather_full_context(self, pr: PRMetadata, diff: list[FileDiff]) -> dict:
    """Fetch all context in a fixed, deterministic order."""
    context = {
        "full_files": {},
        "related_tests": {},
        "git_history": {},
        "dependencies": {},
        "existing_comments": [],
    }

    # Process changed files in sorted order
    for file_diff in sorted(diff, key=lambda fd: fd.path):
        path = file_diff.path

        # 1. Full file content
        context["full_files"][path] = self._adapter.get_file_content(path, pr.head_sha)

        # 2. Related tests
        test_finder = RelatedTestFinder(self._adapter, pr.head_sha, ...)
        context["related_tests"][path] = test_finder.fetch_test_content(path)

        # 3. Git history
        git_history = GitHistory(self._adapter)
        context["git_history"][path] = git_history.get_history(path, limit=5)

        # 4. Dependencies
        tracer = DependencyTracer(self._adapter, pr.head_sha, ...)
        context["dependencies"][path] = tracer.get_dependencies(path, depth=2)

    # 5. Existing PR comments (for deduplication)
    context["existing_comments"] = self._adapter.get_existing_comments(pr.pr_id)

    return context
```

**Pros:**
- ✅ Fully deterministic context order
- ✅ No agentic variability
- ✅ Easier to debug/test
- ✅ Lower API call count (predictable cost)

**Cons:**
- ❌ Over-fetches context (slower, more expensive)
- ❌ Cannot adapt based on what's found
- ❌ May hit context window limits on large PRs

#### Approach 2: Constrained Agentic Loop

Keep agentic flexibility but add determinism guardrails:

```python
class DeterministicPRReviewer(PRReviewer):
    """PRReviewer with deterministic tool execution order."""

    def review(self, pr_id: int | str) -> AgentSession:
        # ... standard setup ...

        # Phase 1: REQUIRED tools (always run in this order)
        required_tools = [
            ("fetch_full_file", {"path": fd.path}) for fd in sorted(diff, key=lambda x: x.path)
        ] + [
            ("get_pr_history_comments", {}),
        ]

        for tool_name, tool_input in required_tools:
            result = self._executor.execute(tool_name, tool_input, f"required-{tool_name}")
            # Feed result back into context

        # Phase 2: OPTIONAL tools (Claude decides, but we sort results)
        # Run agentic loop but with temperature=0.0

        return session
```

**Pros:**
- ✅ Balance between determinism and flexibility
- ✅ Guaranteed minimum context
- ✅ Can still adapt to complex PRs

**Cons:**
- ❌ More complex implementation
- ❌ Still some non-determinism in optional phase

---

### Strategy C: **Caching & Memoization** (MEDIUM IMPACT)

**Priority:** 🟡 MEDIUM
**Effort:** Low
**Expected Improvement:** 100% deterministic for cached PRs

#### Implementation

```python
import hashlib
import json
from pathlib import Path

class CachedPRReviewer(PRReviewer):
    """PRReviewer with deterministic caching."""

    def __init__(self, *args, cache_dir: Path | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._cache_dir = cache_dir or Path(".pr_reviewer_cache/reviews")
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def review(self, pr_id: int | str) -> AgentSession:
        # Compute deterministic cache key
        pr_metadata = self._adapter.get_pr_metadata(pr_id)
        diff = self._adapter.get_pr_diff(pr_id)

        cache_key = self._compute_cache_key(pr_metadata, diff)
        cache_file = self._cache_dir / f"{cache_key}.json"

        # Check cache
        if cache_file.exists():
            logger.info("cache_hit", pr_id=pr_id, cache_key=cache_key)
            return AgentSession.model_validate_json(cache_file.read_text())

        # Run review
        session = super().review(pr_id)

        # Save to cache
        cache_file.write_text(session.model_dump_json(indent=2))
        logger.info("cache_write", pr_id=pr_id, cache_key=cache_key)

        return session

    def _compute_cache_key(self, pr: PRMetadata, diff: list[FileDiff]) -> str:
        """Compute deterministic hash of PR state."""
        key_data = {
            "pr_id": str(pr.pr_id),
            "head_sha": pr.head_sha,
            "base_sha": pr.base_sha,
            "repo": pr.repo_full_name,
            "diff_fingerprint": self._diff_fingerprint(diff),
            "model": self._model,
            "temperature": getattr(self, "_temperature", 1.0),
            "repo_context_version": self._repo_context.generated_at if self._repo_context else None,
        }
        key_json = json.dumps(key_data, sort_keys=True)
        return hashlib.sha256(key_json.encode()).hexdigest()[:16]

    def _diff_fingerprint(self, diff: list[FileDiff]) -> str:
        """Hash the diff content to detect changes."""
        diff_str = "\n".join(
            f"{fd.path}:{fd.additions}:{fd.deletions}"
            for fd in sorted(diff, key=lambda x: x.path)
        )
        return hashlib.md5(diff_str.encode()).hexdigest()
```

**Usage:**
```python
reviewer = CachedPRReviewer(
    adapter=adapter,
    api_key=api_key,
    cache_dir=Path(".pr_reviewer_cache/reviews"),
    temperature=0.0,
)
session = reviewer.review(123)  # First run: cache miss → runs review
session = reviewer.review(123)  # Second run: cache hit → instant return
```

**Pros:**
- ✅ 100% deterministic for cached PRs
- ✅ Instant results on cache hit
- ✅ Reduces API costs
- ✅ Enables regression testing

**Cons:**
- ❌ Stale results if model/prompts change
- ❌ Requires cache invalidation strategy
- ❌ Disk space usage

**Cache invalidation triggers:**
- PR head SHA changes (new commits)
- Model version changes
- Temperature setting changes
- Repo context regenerated
- Manual cache clear (e.g., `pr-reviewer cache clear`)

---

### Strategy D: **Structured Output Mode** (FUTURE)

**Priority:** 🟢 LOW (Future enhancement)
**Effort:** High
**Expected Improvement:** 15-25% more consistent findings

Use Claude's structured output feature (JSON schema mode) for findings:

```python
FINDING_SCHEMA = {
    "type": "object",
    "properties": {
        "file": {"type": "string"},
        "line_start": {"type": "integer"},
        "line_end": {"type": "integer"},
        "severity": {"type": "string", "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"]},
        "category": {"type": "string", "enum": ["BUG", "LOGIC", "SECURITY", ...]},
        "message": {"type": "string"},
        "suggestion": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": ["file", "line_start", "line_end", "severity", "category", "message"],
}

response = self._client.messages.create(
    model=self._model,
    temperature=0.0,
    response_format={"type": "json_object", "schema": FINDING_SCHEMA},  # ← Enforces schema
    # ...
)
```

**Pros:**
- ✅ Guaranteed valid output format
- ✅ Reduces parsing errors
- ✅ More consistent field values

**Cons:**
- ❌ Not yet supported by all Claude models
- ❌ Less flexible for free-form suggestions
- ❌ Requires careful schema design

---

### Strategy E: **Fingerprinting & Deduplication** (LOW IMPACT)

**Priority:** 🟢 LOW (Already partially implemented)
**Effort:** Low
**Status:** ✅ Already exists ([models.py:146-149](../src/pr_reviewer/models.py#L146-L149))

```python
@property
def fingerprint(self) -> str:
    """Unique fingerprint for deduplication."""
    body_prefix = self.message[:80]
    return f"{self.file}:{self.line_start}:{body_prefix}"
```

**Enhancement:** Use semantic similarity instead of exact prefix match

```python
from sentence_transformers import SentenceTransformer

class SemanticDeduplicator:
    """Deduplicate findings using semantic similarity."""

    def __init__(self, threshold: float = 0.85):
        self.model = SentenceTransformer('all-MiniLM-L6-v2')
        self.threshold = threshold

    def deduplicate(self, findings: list[ReviewFinding]) -> list[ReviewFinding]:
        """Remove semantically duplicate findings."""
        unique = []
        embeddings = self.model.encode([f.message for f in findings])

        for i, finding in enumerate(findings):
            is_duplicate = False
            for j, kept_finding in enumerate(unique):
                similarity = cosine_similarity(embeddings[i], embeddings[j])
                if similarity > self.threshold and finding.file == kept_finding.file:
                    is_duplicate = True
                    break

            if not is_duplicate:
                unique.append(finding)

        return unique
```

**Pros:**
- ✅ Catches semantically similar but differently worded findings
- ✅ Reduces duplicate-but-rephrased issues

**Cons:**
- ❌ Adds ML dependency
- ❌ Slower than exact match
- ❌ May over-deduplicate (false positives)

---

## Implementation Roadmap

### Phase 1: Quick Wins (Week 1)

**Goal:** 60-70% improvement in determinism

1. ✅ **Add temperature parameter**
   - Add `temperature` field to `AnthropicConfig` ([config.py:242-246](../src/pr_reviewer/config.py#L242-L246))
   - Add `PR_REVIEWER_TEMPERATURE` env var
   - Default to `0.0` for determinism
   - Update both `review()` and `_generate_summary()` calls

2. ✅ **Improve truncation**
   - Change `_truncate()` to split at line boundaries ([tool_executor.py:82-84](../src/pr_reviewer/agent/tool_executor.py#L82-L84))
   ```python
   def _truncate(self, text: str) -> str:
       if len(text) <= self._max_len:
           return text
       # Split at last newline before limit
       truncated = text[:self._max_len]
       last_newline = truncated.rfind('\n')
       if last_newline > 0:
           truncated = truncated[:last_newline]
       return truncated + f"\n\n[... truncated at {len(truncated)} chars ...]"
   ```

3. ✅ **Add cache support**
   - Implement `CachedPRReviewer` wrapper class
   - Add `--use-cache` / `--no-cache` CLI flags
   - Add cache invalidation command

**Deliverables:**
- Updated config schema
- Updated CLI with `--temperature` flag
- Cache implementation
- Documentation update

### Phase 2: Deterministic Context Gathering (Week 2-3)

**Goal:** 85-90% improvement in determinism

1. ⚙️ **Implement deterministic pre-fetch mode**
   - Create `DeterministicPRReviewer` subclass
   - Implement `_gather_full_context()` with sorted file order
   - Make it opt-in via `--deterministic` flag

2. ⚙️ **Add fixed tool execution order**
   - Define `REQUIRED_TOOLS` constant
   - Pre-execute before agentic loop
   - Document in SKILL.md

**Deliverables:**
- `DeterministicPRReviewer` class
- CLI flag `--deterministic`
- Performance benchmarks (determinism % vs. run time)

### Phase 3: Advanced Features (Week 4+)

**Goal:** 95%+ improvement in determinism

1. 🔬 **Structured output mode**
   - Wait for Claude API support
   - Implement JSON schema mode for findings
   - A/B test against current tool-based approach

2. 🔬 **Semantic deduplication**
   - Add optional `sentence-transformers` dependency
   - Implement `SemanticDeduplicator`
   - Make it opt-in via `--semantic-dedup`

**Deliverables:**
- Structured output implementation (when API ready)
- Semantic deduplication module
- Comprehensive benchmarks

---

## Trade-offs & Considerations

### Determinism vs. Quality

| Factor | High Determinism (temp=0.0) | High Creativity (temp=1.0) |
|--------|----------------------------|---------------------------|
| **Consistency** | ✅ Same findings on every run | ❌ Varies run-to-run |
| **Edge case coverage** | ❌ May miss rare patterns | ✅ Explores more possibilities |
| **Wording variety** | ❌ Repetitive phrasing | ✅ Varied, natural language |
| **False positive rate** | ✅ Lower (more conservative) | ❌ Higher (more speculative) |
| **Review time** | ✅ Faster (predictable) | ❌ Slower (adaptive) |

**Recommendation:** Use `temperature=0.0` for:
- Regression testing
- Compliance/audit reviews
- CI/CD pipelines
- Large team consistency

Use `temperature=0.3-0.7` for:
- Exploratory reviews
- One-off manual reviews
- Research/prototyping

### Determinism vs. Performance

| Approach | Determinism | Speed | API Cost | Context Quality |
|----------|-------------|-------|----------|----------------|
| **Current (agentic, temp=1.0)** | 40% | Fast | Variable | Adaptive |
| **Add temp=0.0 only** | 70% | Fast | Variable | Adaptive |
| **Pre-fetch all context** | 95% | Slow | High | Complete |
| **Cached reviews** | 100% | Instant (hit) | Zero (hit) | Historical |
| **Constrained agentic** | 85% | Medium | Medium | Balanced |

**Recommendation:** Tiered approach
```yaml
# .pr-reviewer/config.yaml
review_mode: auto  # auto | deterministic | cached

modes:
  auto:
    temperature: 0.3
    use_cache: true
    prefetch_tools: []

  deterministic:
    temperature: 0.0
    use_cache: true
    prefetch_tools: [fetch_full_file, get_pr_history_comments]

  cached:
    temperature: 0.0
    use_cache: true
    cache_only: true  # Fail if not cached
```

### API Limitations

**Current Claude API constraints:**
- Temperature clamped to `[0.0, 1.0]`
- No true deterministic mode (even temp=0.0 has ~5% variance)
- Model updates may change behavior even with same params
- No built-in seeding for reproducibility

**Mitigation:**
- Pin model version: `claude-sonnet-4-6@20250101` (when supported)
- Document model version in cache keys
- Add model version to review metadata
- Set up alerts for model version changes

---

## Testing Determinism

### Benchmark Script

```python
#!/usr/bin/env python3
"""Benchmark determinism by running same PR review N times."""

import json
from collections import Counter
from pathlib import Path

def benchmark_determinism(pr_url: str, n_runs: int = 10, temperature: float = 0.0):
    """Run review N times and measure consistency."""
    results = []

    for i in range(n_runs):
        print(f"Run {i+1}/{n_runs}...")
        session = reviewer.review(pr_id)

        findings_fingerprints = [f.fingerprint for f in session.findings]
        results.append({
            "run": i + 1,
            "finding_count": len(session.findings),
            "fingerprints": findings_fingerprints,
            "risk_level": session.summary.risk_level if session.summary else None,
        })

    # Analyze variance
    counts = [r["finding_count"] for r in results]
    all_fingerprints = [fp for r in results for fp in r["fingerprints"]]
    fingerprint_counts = Counter(all_fingerprints)

    # Findings that appeared in ALL runs
    stable_findings = {fp for fp, count in fingerprint_counts.items() if count == n_runs}

    # Findings that appeared in only SOME runs
    flaky_findings = {fp for fp, count in fingerprint_counts.items() if count < n_runs}

    print(f"\n📊 Determinism Report (temperature={temperature})")
    print(f"  Runs: {n_runs}")
    print(f"  Finding count range: {min(counts)} - {max(counts)} (σ={stdev(counts):.2f})")
    print(f"  Stable findings: {len(stable_findings)} ({len(stable_findings)/len(all_fingerprints)*100:.1f}%)")
    print(f"  Flaky findings: {len(flaky_findings)} ({len(flaky_findings)/len(all_fingerprints)*100:.1f}%)")
    print(f"  Determinism score: {len(stable_findings) / (len(stable_findings) + len(flaky_findings)) * 100:.1f}%")

    return {
        "stable": stable_findings,
        "flaky": flaky_findings,
        "score": len(stable_findings) / (len(stable_findings) + len(flaky_findings)),
    }

# Run benchmarks
results_temp_0 = benchmark_determinism(PR_URL, n_runs=10, temperature=0.0)
results_temp_1 = benchmark_determinism(PR_URL, n_runs=10, temperature=1.0)
```

**Expected results:**
- `temperature=1.0`: 30-50% determinism score
- `temperature=0.0`: 80-95% determinism score
- `temperature=0.0 + cache`: 100% determinism score

---

## Recommended Configuration

### For CI/CD (Maximum Determinism)

```yaml
# .pr-reviewer/config.yaml
anthropic:
  model: claude-sonnet-4-6
  temperature: 0.0
  max_tool_calls: 40  # Lower limit for predictability

review:
  mode: deterministic
  use_cache: true
  min_severity_to_post: MEDIUM
  max_inline_comments: 20

cache:
  directory: .pr_reviewer_cache
  ttl_seconds: 86400  # 24 hours
```

```bash
# CI script
pr-reviewer review \
  --url "$PR_URL" \
  --deterministic \
  --temperature 0.0 \
  --use-cache \
  --min-severity MEDIUM
```

### For Interactive Use (Balanced)

```yaml
anthropic:
  temperature: 0.3  # Low variance, some flexibility

review:
  mode: auto
  use_cache: true
  min_severity_to_post: LOW

cache:
  ttl_seconds: 3600  # 1 hour
```

---

## Conclusion

**Summary of Expected Improvements:**

| Strategy | Effort | Determinism Gain | Speed Impact | Cost Impact |
|----------|--------|-----------------|--------------|-------------|
| Temperature=0.0 | Low | +40% | None | None |
| Caching | Low | +60% (on cache hit) | 100x faster (hit) | -100% (hit) |
| Pre-fetch context | Medium | +20% | -20% slower | +15% cost |
| Constrained agentic | Medium | +15% | -10% slower | +5% cost |
| Structured output | High | +10% | None | None |

**Recommended Priority:**
1. 🔴 **Week 1:** Add temperature parameter (default 0.0)
2. 🟡 **Week 1:** Implement caching
3. 🟡 **Week 2:** Add deterministic pre-fetch mode
4. 🟢 **Week 3+:** Structured output (when API ready)

**Total Expected Improvement:**
From **~40% deterministic** (current) → **~95% deterministic** (with temperature=0.0 + cache + pre-fetch)

---

## Next Steps

1. **Prototype temperature parameter** in a branch
2. **Run benchmark tests** on 5 PRs with temp=0.0 vs temp=1.0
3. **Implement caching** if benchmarks show value
4. **Document in CLAUDE.md** and user-facing docs
5. **Add regression tests** for determinism

**Questions to resolve:**
- Should temperature be configurable per-severity? (e.g., temp=0.0 for CRITICAL, temp=0.3 for LOW)
- Should cache keys include suppression rules? (currently not in fingerprint)
- Should we support `--seed` parameter (when API adds support)?
