---
name: pr-review
description: "PRGenie — Perform a production-grade code review on a GitHub or Bitbucket PR. Usage: /pr-review <PR-URL> [--dry-run] [--min-severity LOW|MEDIUM|HIGH|CRITICAL]"
---

Perform a thorough, context-aware pull request code review on: $ARGUMENTS

---

## Setup — Parse Arguments

Extract from `$ARGUMENTS`:
- `PR_URL`: the pull request URL (required)
- `DRY_RUN`: true if `--dry-run` is present (default: false)
- `MIN_SEVERITY`: minimum severity to post — INFO / LOW / MEDIUM / HIGH / CRITICAL (default: LOW)

Determine platform:
- If URL matches `github.com` → platform = **GitHub**
- If URL matches `bitbucket.org` → platform = **Bitbucket**

For GitHub, parse: `https://github.com/{OWNER}/{REPO}/pull/{PR_NUMBER}`
For Bitbucket, parse: `https://bitbucket.org/{WORKSPACE}/{REPO}/pull-requests/{PR_ID}`

---

## Phase 0 — Load Repo Context (if available)

Before starting the review, check for stored repo context:

1. Try `cat .pr-reviewer/repo_context.json 2>/dev/null`
2. If empty, derive `<owner>` and `<repo>` from the PR URL and try:
   `cat ~/.pr-reviewer/contexts/<owner>/<repo>/repo_context.json 2>/dev/null`
3. If found, parse the JSON. Throughout **all** analysis passes:
   - Apply `review_hints` — these are known codebase pitfalls; check every changed file
   - Flag any changed files under `security_sensitive_paths` with at minimum a MEDIUM finding
   - Check new functions/classes against `naming_conventions`
   - Verify new tests follow `test_conventions`
   - Use `architecture_notes` to assess whether the PR fits the established pattern
4. If not found, append to the review summary:
   > 💡 **Tip:** Run `/repo-onboard` to generate repo context for richer,
   > convention-aware reviews.

5. Try to load suppressions:
   - `cat .pr-reviewer/suppressions.json 2>/dev/null`
   - If empty, try: `cat ~/.pr-reviewer/contexts/<owner>/<repo>/suppressions.json 2>/dev/null`
   - If found: parse JSON, filter out entries where `expires_at` is set and `expires_at < today` → store as `ACTIVE_SUPPRESSIONS`
   - If not found: `ACTIVE_SUPPRESSIONS = []`

6. Load external context providers:
   ```bash
   # List all provider output files in the drop-zone
   ls .pr-reviewer/context/*.json 2>/dev/null
   ```
   For each file found, read and parse it. Store as `EXTERNAL_CONTEXT` keyed by `source`.
   Each file follows the context provider schema:
   ```json
   {
     "source": "<provider name>",
     "version": "1.0",
     "findings": [
       {"file": "...", "line": 1, "severity": "HIGH", "category": "SECURITY",
        "message": "...", "rule_id": "...", "url": "..."}
     ],
     "summary": {"passed": true, "label": "OK", "critical": 0, "high": 1, "medium": 3, "low": 0}
   }
   ```
   If no files exist: `EXTERNAL_CONTEXT = {}`

---

## Phase 1 — Fetch PR Metadata & Diff

### GitHub
```bash
gh pr view "$PR_URL" --json number,title,body,author,headRefName,baseRefName,headRefOid,baseRefOid,isDraft,url
gh pr diff "$PR_URL"
```

If `isDraft` is true, print a warning but continue unless the user passes `--skip-drafts`.

### Bitbucket
```bash
curl -s -u "$BITBUCKET_USERNAME:$BITBUCKET_APP_PASSWORD" \
  "https://api.bitbucket.org/2.0/repositories/$WORKSPACE/$REPO/pullrequests/$PR_ID"
curl -s -u "$BITBUCKET_USERNAME:$BITBUCKET_APP_PASSWORD" \
  "https://api.bitbucket.org/2.0/repositories/$WORKSPACE/$REPO/pullrequests/$PR_ID/diff"
```

Store:
- `HEAD_SHA` = headRefOid (GitHub) or source.commit.hash (Bitbucket)
- `BASE_SHA` = baseRefOid (GitHub) or destination.commit.hash (Bitbucket)
- `DIFF_TEXT` = full unified diff output
- `PR_TITLE`, `PR_BODY`, `PR_AUTHOR`, `SOURCE_BRANCH`, `TARGET_BRANCH`

Parse the diff to get the list of changed files with their additions/deletions.

---

## Phase 2 — Gather Deep Context

For **each changed file** in the diff:

### 2a. Fetch full file content at HEAD
**GitHub:**
```bash
gh api "repos/$OWNER/$REPO/contents/$FILE_PATH?ref=$HEAD_SHA" \
  --jq '.content' | base64 -d
```
If the file is too large (> 1 MB), fetch via raw URL:
```bash
curl -s -H "Authorization: token $GH_TOKEN" \
  "https://raw.githubusercontent.com/$OWNER/$REPO/$HEAD_SHA/$FILE_PATH"
```

**Bitbucket:**
```bash
curl -s -u "$BITBUCKET_USERNAME:$BITBUCKET_APP_PASSWORD" \
  "https://api.bitbucket.org/2.0/repositories/$WORKSPACE/$REPO/src/$HEAD_SHA/$FILE_PATH"
```

### 2b. Find related test files
For each source file `src/foo/bar.ts`, check whether any of these exist (use `gh api` tree or `ls`):
1. `tests/test_bar.ts` or `tests/bar.test.ts`
2. `tests/unit/bar.test.ts`
3. `src/foo/bar.test.ts` or `src/foo/__tests__/bar.test.ts`
4. Mirror: `src/foo/bar.ts` → `tests/foo/bar.test.ts`

Fetch any that exist.

### 2c. Fetch recent git history for context
```bash
gh api "repos/$OWNER/$REPO/commits?path=$FILE_PATH&per_page=5" \
  --jq '[.[] | {sha: .sha[0:8], message: .commit.message | split("\n")[0], author: .commit.author.name, date: .commit.author.date}]'
```

### 2d. Fetch existing PR comments (for deduplication)
**GitHub:**
```bash
gh api "repos/$OWNER/$REPO/pulls/$PR_NUMBER/comments" \
  --jq '[.[] | {path: .path, line: .line, body: .body[0:100]}]'
```

---

## Phase 3 — Perform the Code Review

**Before the five passes:** if `EXTERNAL_CONTEXT` is non-empty, seed the finding list with
external provider findings that touch changed files:
- For each provider in `EXTERNAL_CONTEXT`, for each finding whose `file` matches a changed file,
  promote it into the candidate finding list, tagged `source: <provider>` (e.g. `source: snyk`)
- External findings skip the confidence threshold check — they are pre-vetted by the tool
- During the five passes, if Claude independently identifies the same issue as an external
  finding (same file, similar message), merge them: keep the external finding's `rule_id`/`url`,
  use Claude's message if it is more descriptive, do not emit a duplicate

Now apply the following five passes in order:

### Pass 1 — INTENT
Read the PR title, description, and the diff overview. Determine:
- What problem is this PR solving?
- Does the implementation match the stated intent?
- Are there any obvious scope issues (too much / too little change)?

### Pass 2 — LOGIC & BUGS
Carefully examine every changed file for:
- Logic errors, incorrect conditionals, off-by-one errors
- Null/undefined dereferences, missing null checks
- Incorrect error handling or silent failures
- Race conditions or timing issues
- API contract violations (wrong argument types, wrong return types)
- Edge cases: empty arrays, zero values, negative numbers, very large inputs

### Pass 3 — SECURITY
Check for:
- Injection vulnerabilities (SQL, command, template, XSS)
- Path traversal (user-controlled file paths used without sanitization)
- Authentication/authorization bypass
- Sensitive data in logs, error messages, or responses
- Unsafe deserialization or `eval`-like constructs
- Missing input validation at trust boundaries

### Pass 4 — CODE QUALITY & STYLE
Check against the project's established patterns (inferred from the fetched files):
- Does the new code follow the same conventions as surrounding code?
- Are there unnecessary abstractions or over-engineering?
- Dead code, unused variables, leftover debug statements
- Missing or incorrect TypeScript types (if TypeScript project)
- Performance issues (N+1 queries, unnecessary re-renders, blocking I/O)

### Pass 5 — TESTS
- Are new public functions/classes covered by tests?
- Do existing tests still correctly cover the changed code?
- For each new untested public function, generate a concise test stub

---

## Phase 4 — Compile Findings

For each issue found, create a structured finding:
```
FILE: <repo-relative path>
LINE: <line number in the NEW file>
SEVERITY: CRITICAL | HIGH | MEDIUM | LOW | INFO
CATEGORY: BUG | LOGIC | SECURITY | PERFORMANCE | MAINTAINABILITY | MISSING_TEST | STYLE
MESSAGE: <clear, specific description of the issue>
SUGGESTION: <concrete fix, ideally with corrected code>
CONFIDENCE: <0.0–1.0>
```

**Rules:**
- Only emit findings with confidence ≥ 0.5
- Reserve CRITICAL/HIGH for real bugs and security issues
- Always include a concrete suggestion with corrected code when possible
- Skip findings that already appear in the existing PR comments (deduplication)
- Cap at 30 inline findings total; if more exist, include extras only in the summary

**Suppression rules (applied after deduplication):**
- **NEVER suppress** findings where `severity == CRITICAL` (safety invariant — hard rule)
- **NEVER suppress** findings where `category == SECURITY` (safety invariant — hard rule)
- For all other findings, check each against `ACTIVE_SUPPRESSIONS`. A suppression matches when ALL of:
  1. The finding's message semantically matches the suppression `pattern` (plain-English phrase match)
  2. If `scope` is set: the finding's file path starts with the suppression `scope`
  3. If `category` is set: the finding's category equals the suppression `category`
- If matched: **exclude** the finding from `pr_comments.json`; add to `SUPPRESSED` list:
  ```json
  {"finding_summary": "<SEVERITY> [<CATEGORY>] <short message> `<file>`", "suppression_id": "<id>", "reason": "<reason>"}
  ```
- If `ACTIVE_SUPPRESSIONS` is empty, skip suppression checks entirely

---

## Phase 5 — Post Comments

### Determine what to post
Filter findings where `SEVERITY >= MIN_SEVERITY`.

### Write findings to JSON
Write all filtered findings to a temp file `/tmp/pr_comments.json` using the schema below.
Write the summary markdown to `/tmp/pr_summary.md`.

**comments JSON schema** (array):
```json
[
  {
    "path": "packages/foo/bar.ts",
    "line": 42,
    "severity": "HIGH",
    "category": "SECURITY",
    "message": "Description of issue",
    "suggestion_lang": "typescript",
    "suggestion": "// corrected code (optional — omit if no code block)",
    "suggestion_text": "Plain-text suggestion when no code block is needed (optional)",
    "confidence": 95
  }
]
```

**summary.md** — use this format:
```markdown
# 🤖 PRGenie — Code Review Summary

**Risk Level:** {RISK_EMOJI} {RISK_LEVEL}

## Overview
{2-3 sentence summary}

## PR Intent
{One sentence}

## Key Concerns
- {concern 1}

## 🔕 Suppressed Findings ({N})
<!-- Include this section only when SUPPRESSED is non-empty -->
| Finding | Suppression ID | Reason |
|---|---|---|
| MEDIUM [MAINTAINABILITY] raw JSON.parse… `util/filesystem.ts` | sup-001 | Zod migration in progress |

## Suggested Test Stubs
### `test_name`
*Source:* `source_file`
```typescript
// stub
```

## General Suggestions
- {suggestion}

## External Checks
<!-- Include only when EXTERNAL_CONTEXT is non-empty -->
| Tool | Status | Critical | High | Medium | Low |
|---|---|---|---|---|---|
| Snyk | ✅ Pass | 0 | 1 | 3 | 2 |
| SonarQube | ❌ Fail (C) | 0 | 2 | 5 | 1 |
| JIRA | ⚠️ FAIL | — | — | — | — |

---
<sub>Generated by PRGenie via [Claude Code](https://claude.ai/claude-code)</sub>
```

### Call post_review.py
After writing the files, invoke the generic posting utility bundled with this skill.
`SKILL_DIR` is the directory containing this SKILL.md file (resolved at runtime).

```bash
# GitHub
python "$SKILL_DIR/post_review.py" \
  --pr-url "$PR_URL" \
  --comments /tmp/pr_comments.json \
  --summary /tmp/pr_summary.md \
  --gh-path "$(which gh)" \
  [--dry-run]   # include only if DRY_RUN is true

# Bitbucket — post_review.py is GitHub-only for now.
# For Bitbucket, fall back to curl as described below.
```

`post_review.py` handles everything in **4 API calls total** (constant, regardless of the number of findings):

1. `gh pr view --json headRefOid` — fetch HEAD commit SHA (skipped if `--head-sha` supplied)
2. `gh pr diff` — fetch the unified diff to build a line→position map locally
3. `POST /repos/{owner}/{repo}/pulls/{pr}/reviews` — submit **all** inline comments in one batch
4. `gh pr comment` — post the top-level summary

**Why `position` matters:** GitHub's batch review API requires each inline comment to use
`position` (1-based line offset within the diff hunk), not the `line` + `side` fields used by
the single-comment endpoint. Using `line` causes the API to silently drop every comment.
`post_review.py` calls `build_position_map()` internally to parse the diff and resolve
`(file_path, new_line_number) → position` before submitting. Any comment whose line falls
outside the diff (e.g. unchanged context beyond the hunk) is skipped with a warning.

**📊 Review Value Metrics:** `post_review.py` automatically appends a **Review Value Metrics**
section to the summary before posting, estimating defect prevention value and reviewer time
saved based on IBM/NIST cost-of-defect research. Example output:

```markdown
---

## 📊 Review Value Metrics

| Metric | Value |
|---|---|
| Files reviewed | 2 |
| Lines of diff | 66 added / 3 removed |
| Findings | 15 (3 🚨 critical · 3 🔴 high · 5 🟡 medium · 3 🔵 low) |
| Avg confidence | 95% |
| **Est. defect prevention value** | **~$45,750** |
| **Est. reviewer time saved** | **~2 hrs (~$400)** |

<sub>Defect prevention estimates based on IBM/NIST cost-of-defect research (fixing in review:
~$150 vs. production: ~$10K–$50K). Tune with `--cost-critical`, `--cost-high`,
`--cost-medium`, `--cost-low`, `--hourly-rate`.</sub>
```

To suppress the metrics section, pass `--no-metrics`. To tune cost assumptions for your
organisation:
```bash
python "$SKILL_DIR/post_review.py" \
  --pr-url "$PR_URL" \
  --comments /tmp/pr_comments.json \
  --summary /tmp/pr_summary.md \
  --cost-critical 50000 \
  --cost-high 5000 \
  --hourly-rate 300
```

### Bitbucket fallback (no post_review.py support yet)
For each finding:
```bash
curl -s -X POST \
  -u "$BITBUCKET_USERNAME:$BITBUCKET_APP_PASSWORD" \
  -H "Content-Type: application/json" \
  "https://api.bitbucket.org/2.0/repositories/$WORKSPACE/$REPO/pullrequests/$PR_ID/comments" \
  -d "{\"content\":{\"raw\":\"$BODY\"},\"inline\":{\"to\":$LINE,\"path\":\"$FILE\"}}"
```
Then post the summary:
```bash
curl -s -X POST \
  -u "$BITBUCKET_USERNAME:$BITBUCKET_APP_PASSWORD" \
  -H "Content-Type: application/json" \
  "https://api.bitbucket.org/2.0/repositories/$WORKSPACE/$REPO/pullrequests/$PR_ID/comments" \
  -d "{\"content\":{\"raw\":\"$SUMMARY_MARKDOWN\"}}"
```

---

## Phase 6 — Report Results

After posting (or dry-running), print a summary to the terminal:

```
✅ PR Review Complete
   PR:              {TITLE} #{NUMBER}
   Platform:        {github|bitbucket}
   Risk level:      {RISK_LEVEL}
   Findings:        {N} total ({CRITICAL} critical, {HIGH} high, {MEDIUM} medium, {LOW} low)
   Suppressed:      {N} findings matched active suppressions (see summary for details)
   External:        {provider1}: ✅ Pass | {provider2}: ❌ Fail  (or: none loaded)
   Inline comments: {N} posted (or [dry run])
   Summary:         posted (or [dry run])
```

If any inline comment failed to post (e.g. line not in diff), note it but continue.
