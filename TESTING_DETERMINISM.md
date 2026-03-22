# Testing Determinism on PR #2

This guide shows how to test the deterministic improvements on https://github.com/psinghjpm/opencode/pull/2

---

## Quick Start (Recommended)

**Best approach:** Use the `/pr-review` skill in Claude Code (uses your Claude Pro subscription, no API key needed)

### Steps:

1. **Open Claude Code** in any terminal
2. **Run the review 3 times:**

```bash
# Run 1
/pr-review https://github.com/psinghjpm/opencode/pull/2 --dry-run > review_run1.txt 2>&1

# Run 2
/pr-review https://github.com/psinghjpm/opencode/pull/2 --dry-run > review_run2.txt 2>&1

# Run 3
/pr-review https://github.com/psinghjpm/opencode/pull/2 --dry-run > review_run3.txt 2>&1
```

3. **Compare the outputs:**

```bash
# Count findings in each run
grep -c "SEVERITY" review_run1.txt
grep -c "SEVERITY" review_run2.txt
grep -c "SEVERITY" review_run3.txt

# Or manually compare the files
code --diff review_run1.txt review_run2.txt
code --diff review_run2.txt review_run3.txt
```

4. **Expected result:**

With `temperature=0.0` and `min_confidence=0.8`, you should see:
- ✅ **Same number of findings** across all 3 runs
- ✅ **Same findings** (same file, line, severity, category)
- ✅ **Similar wording** (may have minor variations due to LLM internals)
- ✅ **Determinism ~80-90%** (vs. ~40% with old config)

---

## Alternative: Standalone Testing (Requires API Keys)

If you have `ANTHROPIC_API_KEY` and `GITHUB_TOKEN`, you can run the automated test:

### Setup (One-time)

```powershell
# PowerShell
$env:ANTHROPIC_API_KEY = "sk-ant-..."
$env:GITHUB_TOKEN = "ghp_..."

# OR create config.yaml
cp config.enterprise.yaml config.yaml
# Edit config.yaml and add your API keys
```

### Run Automated Test

```bash
# Run 3 reviews and compare automatically
python run_determinism_test.py
```

This will:
1. Run the review 3 times with enterprise config (`temperature=0.0`, `min_confidence=0.8`)
2. Save outputs to `determinism_test_results/<timestamp>/`
3. Compare findings across runs
4. Print a determinism report

### Expected Output

```
==================================================
PRGenie Determinism Test
==================================================
PR: https://github.com/psinghjpm/opencode/pull/2
Runs: 3
Config: temperature=0.0, min_confidence=0.8, min_severity=MEDIUM

==================================================
Run 1/3
==================================================
  Duration: 82.3s
  Findings: 8
  Posted: 5
  Filtered (low confidence): 2
  Status: SUCCESS

==================================================
Run 2/3
==================================================
  Duration: 79.1s
  Findings: 8
  Posted: 5
  Filtered (low confidence): 2
  Status: SUCCESS

==================================================
Run 3/3
==================================================
  Duration: 81.7s
  Findings: 8
  Posted: 5
  Filtered (low confidence): 2
  Status: SUCCESS

==================================================
SUMMARY
==================================================

Successful runs: 3/3

✅ DETERMINISTIC: All runs posted 5 findings
✅ CONSISTENT: All runs found 8 total findings

Durations:
  Run 1: 82.3s
  Run 2: 79.1s
  Run 3: 81.7s
  Average: 81.0s

==================================================
Results saved to: determinism_test_results/20260321_203045
==================================================
```

---

## What to Look For

### ✅ Good Signs (Deterministic)

1. **Exact same number of findings** across all runs
   ```
   Run 1: 8 total, 5 posted
   Run 2: 8 total, 5 posted  ← SAME
   Run 3: 8 total, 5 posted  ← SAME
   ```

2. **Same findings in same files at same lines**
   ```
   Run 1: HIGH [SECURITY] packages/opencode/src/util/filesystem.ts:203
   Run 2: HIGH [SECURITY] packages/opencode/src/util/filesystem.ts:203  ← SAME
   Run 3: HIGH [SECURITY] packages/opencode/src/util/filesystem.ts:203  ← SAME
   ```

3. **Consistent severity/category assignments**
   ```
   Finding: "Missing error handling in readJsonSafe"
   Run 1: MEDIUM [BUG]
   Run 2: MEDIUM [BUG]  ← SAME
   Run 3: MEDIUM [BUG]  ← SAME
   ```

4. **Consistent confidence scores** (± 0.05 is acceptable)
   ```
   Run 1: confidence: 0.87
   Run 2: confidence: 0.87  ← SAME
   Run 3: confidence: 0.86  ← ACCEPTABLE (within 0.05)
   ```

### ❌ Bad Signs (Non-Deterministic)

1. **Different number of findings**
   ```
   Run 1: 12 total
   Run 2: 9 total   ← BAD (3 findings disappeared)
   Run 3: 14 total  ← BAD (different again)
   ```

2. **Findings appear/disappear randomly**
   ```
   Run 1: Finding at line 203
   Run 2: (missing)  ← BAD
   Run 3: Finding at line 203
   ```

3. **Severity flapping**
   ```
   Run 1: HIGH [SECURITY]
   Run 2: MEDIUM [BUG]  ← BAD (changed severity)
   ```

---

## Manual Comparison

If you want to manually inspect the findings:

### 1. Run reviews and save outputs

```bash
# Run 1
/pr-review https://github.com/psinghjpm/opencode/pull/2 --dry-run > run1.txt 2>&1

# Run 2
/pr-review https://github.com/psinghjpm/opencode/pull/2 --dry-run > run2.txt 2>&1
```

### 2. Extract findings sections

```bash
# Extract just the findings (ignore timestamps/logs)
grep -A 5 "SEVERITY\|CATEGORY\|MESSAGE" run1.txt > run1_findings.txt
grep -A 5 "SEVERITY\|CATEGORY\|MESSAGE" run2.txt > run2_findings.txt
```

### 3. Compare

```bash
# Side-by-side diff
diff run1_findings.txt run2_findings.txt

# Or use a visual diff tool
code --diff run1_findings.txt run2_findings.txt
```

### 4. Count matches

```bash
# How many lines are identical?
diff -u run1_findings.txt run2_findings.txt | grep -c "^-" # Lines only in run1
diff -u run1_findings.txt run2_findings.txt | grep -c "^+" # Lines only in run2

# Perfect match = 0 differences
```

---

## PR #2 Expected Findings (Based on Code Review)

The PR adds 4 new functions to `filesystem.ts`:

1. **`readJsonSafe<T>`** — Returns `null` on parse errors
2. **`copyFile(src, dest)`** — Copies files
3. **`readLines(p)`** — Reads lines from text file
4. **`pr --NoLaunch`** flag — Checks out PR without launching TUI

### Potential Issues to Watch For

#### High Confidence (Should appear in all runs)

1. **Missing error handling** (MEDIUM [BUG], confidence ~0.85)
   ```typescript
   // Line 203-207: readJsonSafe catches errors but logs them
   // Should this log be a warning or silent?
   ```

2. **Missing null check** (MEDIUM [BUG], confidence ~0.82)
   ```typescript
   // Line 215: readLines calls readText but doesn't handle if readText throws
   ```

3. **No test coverage** (MEDIUM [MISSING_TEST], confidence ~0.88)
   ```
   New functions readJsonSafe, copyFile, readLines have no tests
   ```

#### Medium Confidence (May or may not appear)

4. **Logging in error path** (LOW [MAINTAINABILITY], confidence ~0.72)
   ```typescript
   // Line 204: Should silent failures really be logged?
   // Depends on use case
   ```

5. **Missing JSDoc** (LOW [MAINTAINABILITY], confidence ~0.65)
   ```
   New functions have JSDoc but could be more detailed
   ```

#### Low Confidence (Should be filtered out with min_confidence=0.8)

6. **Variable naming** (LOW [STYLE], confidence ~0.58)
   ```
   Variable 'p' could be more descriptive
   ```

---

## Troubleshooting

### "Reviews are too different (< 70% match)"

**Possible causes:**

1. **Temperature not set to 0.0**
   ```bash
   # Check config
   grep temperature config.yaml

   # Should show: temperature: 0.0
   # If not, set it:
   export PR_REVIEWER_TEMPERATURE=0.0
   ```

2. **Different model versions**
   ```bash
   # Check logs for model used
   grep "model" determinism_test_results/*/run_*/stderr.log

   # Should all be: claude-sonnet-4-6
   ```

3. **Network/API errors causing retries**
   ```bash
   # Check for error messages in logs
   grep -i "error\|retry\|timeout" determinism_test_results/*/run_*/stderr.log
   ```

### "Reviews are taking >3 minutes"

**Solutions:**

1. **Reduce max_tool_calls**
   ```yaml
   anthropic:
     max_tool_calls: 30  # Down from 40
   ```

2. **Reduce context length**
   ```yaml
   review:
     max_content_length: 8000  # Down from 10000
   ```

### "Getting different findings each time"

**Debug checklist:**

1. ✅ Temperature is `0.0`
2. ✅ Same model version
3. ✅ No API errors/retries
4. ✅ Same config file
5. ✅ No changes to PR between runs

If all above are true but still getting variance:
- **Expected variance: 10-20%** (LLM internals not 100% deterministic)
- **If variance > 30%:** File a bug report with logs

---

## Results Interpretation

### Determinism Score Guide

| Score | Rating | Interpretation |
|-------|--------|----------------|
| **95-100%** | Excellent | Near-perfect determinism |
| **80-94%** | Good | Expected range for temp=0.0 |
| **60-79%** | Fair | Some issues, check config |
| **40-59%** | Poor | Not deterministic, config problem |
| **< 40%** | Failing | Broken, investigate immediately |

### Comparison with Old Config

| Config | Expected Determinism |
|--------|---------------------|
| **Old (temp=1.0, conf=0.5)** | 30-50% |
| **New (temp=0.0, conf=0.8)** | 80-95% |

**Improvement:** **~2x more deterministic** ✅

---

## Next Steps After Testing

1. **If determinism is good (>80%):**
   - ✅ Deploy to production
   - ✅ Monitor for 1 week
   - ✅ Collect developer feedback

2. **If determinism is fair (60-80%):**
   - ⚙️ Raise `min_confidence` to 0.85
   - ⚙️ Lower `max_tool_calls` to 30
   - ⚙️ Re-test

3. **If determinism is poor (<60%):**
   - ❌ Do NOT deploy
   - 🐛 File bug report with logs
   - 🔬 Investigate config/environment

---

## Files Created for Testing

- [run_determinism_test.py](run_determinism_test.py) — Automated test script
- [test_determinism.py](test_determinism.py) — Findings comparison analyzer
- [run_determinism_test.sh](run_determinism_test.sh) — Bash version (for Linux/Mac)
- [TESTING_DETERMINISM.md](TESTING_DETERMINISM.md) — This file

**Ready to test!** Start with the "Quick Start" section above.
