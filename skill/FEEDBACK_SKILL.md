---
name: pr-feedback
description: "Harvest dismissed/resolved PRGenie comments from a PR and write suppression entries to suppressions.json. Usage: /pr-feedback <PR-URL> [--dry-run] [--output PATH]"
---

Harvest dismissed PRGenie findings from: $ARGUMENTS

---

## Setup — Parse Arguments

Extract from `$ARGUMENTS`:
- `PR_URL`: the GitHub PR URL (required) — format `https://github.com/{OWNER}/{REPO}/pull/{PR_NUMBER}`
- `DRY_RUN`: true if `--dry-run` is present (default: false)
- `OUTPUT`: path to write `suppressions.json` (optional)

Parse `OWNER`, `REPO`, `PR_NUMBER` from `PR_URL`.

Determine `OUTPUT` path:
1. If `--output PATH` was supplied, use that path
2. Else if `.pr-reviewer/suppressions.json` exists locally, use it
3. Else use local default: `.pr-reviewer/suppressions.json`
   (fall back to global `~/.pr-reviewer/contexts/<owner>/<repo>/suppressions.json` only when
   the local `.pr-reviewer/` directory does not exist and the global path already has a file)

Compute `TODAY` = current ISO date (YYYY-MM-DD).

---

## Phase 1 — Fetch Review Threads via GraphQL

```bash
gh api graphql \
  -f query='query($owner:String!,$repo:String!,$pr:Int!){
    repository(owner:$owner,name:$repo){
      pullRequest(number:$pr){
        reviewThreads(first:100){
          nodes{
            isResolved
            comments(first:10){
              nodes{
                databaseId
                body
                author{ login }
              }
            }
          }
        }
      }
    }
  }' \
  -f owner=OWNER -f repo=REPO -F pr=PR_NUMBER
```

Store the full response as `THREADS`.

---

## Phase 2 — Identify Dismissed Threads

For each thread in `THREADS.data.repository.pullRequest.reviewThreads.nodes`:

### 2a. Find the PRGenie comment
A comment is a PRGenie comment if its `body` contains the string `🤖 PRGenie`.
If no comment in the thread contains `🤖 PRGenie`, **skip this thread entirely**.

### 2b. Classify as dismissed if ANY of:

1. **Resolved:** `isResolved: true`

2. **NAI reply:** Any comment in the thread (other than the PRGenie comment itself)
   contains one of the following phrases (case-insensitive):
   - `NAI`
   - `not an issue`
   - `false positive`
   - `won't fix`
   - `wont fix`
   - `by design`
   - `not applicable`
   - `ignore`
   - `intentional`
   - `accepted risk`

**Note:** 👍 reactions are intentionally NOT treated as a dismissal signal. A thumbs-up on
a finding more naturally means "good catch / I agree" — the opposite of dismissal. Only
explicit resolve or a typed NAI reply carries unambiguous dismissal intent.

### 2c. Extract dismissal metadata for each dismissed thread

**Finding details** — parse from the PRGenie comment body:
- `severity`: look for a line/header containing `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`, or `INFO`
  (the comment format uses bold text like `**HIGH**` or emoji markers)
- `category`: look for `BUG`, `LOGIC`, `SECURITY`, `PERFORMANCE`, `MAINTAINABILITY`,
  `MISSING_TEST`, or `STYLE`
- `file_path`: look for a code span (backtick) containing a file path, or the `path:` field
  in the comment header
- `message`: first non-header, non-code paragraph of the comment body (strip markdown,
  max 120 chars)

**Dismissal reason** (in priority order):
- If NAI reply: use the reply text (max 200 chars, strip markdown)
- If resolved with no reply: `"resolved without comment"`

**Dismissed by:**
- NAI reply → username of the reply author
- Resolved only → `"unknown"` (GitHub GraphQL `isResolved` doesn't expose who resolved)

---

## Phase 3 — Generate Suppression Candidates

For each dismissed thread from Phase 2:

**Safety invariants (hard rules — cannot be overridden):**
- If `severity == CRITICAL` → **skip, do not generate a suppression candidate**
- If `category == SECURITY` → **skip, do not generate a suppression candidate**

For all other dismissed findings, generate:

```json
{
  "id": "sup-<YYYYMMDD>-<N>",
  "pattern": "<message, max 120 chars, markdown stripped>",
  "category": "<CATEGORY>",
  "scope": "<parent directory of file_path — e.g. packages/opencode/src/util/ for packages/opencode/src/util/filesystem.ts>",
  "reason": "<dismissal reason, max 200 chars>",
  "added_by": "<dismissed_by username>",
  "added_at": "<TODAY>",
  "expires_at": null,
  "source_pr": <PR_NUMBER>
}
```

Where `<N>` is a sequential counter starting at 1 for this run (e.g. `sup-20260228-1`,
`sup-20260228-2`, …).

`scope` is the directory containing the file — use the parent path up to but not including
the filename. If `file_path` is unknown, omit the `scope` field.

---

## Phase 4 — Merge into suppressions.json

### 4a. Load existing file
```bash
cat OUTPUT_PATH 2>/dev/null
```
If the file exists and is valid JSON, parse it. Otherwise start from:
```json
{"version": "1.0", "suppressions": []}
```

### 4b. Deduplicate
For each candidate from Phase 3, check whether any existing entry has the same
`pattern` AND `scope` AND `category`. If so, skip it (it's already suppressed) and
increment `already_existed` counter.

### 4c. Write
Append new entries to `suppressions` array.

If `DRY_RUN` is false:
- Ensure the parent directory exists: `mkdir -p <parent of OUTPUT_PATH>`
- Write the merged JSON back to `OUTPUT_PATH` with 2-space indentation

If `DRY_RUN` is true:
- Print the candidate entries to stdout
- Do **not** write any file

---

## Phase 5 — Report

Print to terminal:

```
✅ pr-feedback complete
   PR:                 OWNER/REPO#PR_NUMBER
   Threads analysed:   N
   Dismissed threads:  N
   New suppressions:   N  (N already existed — skipped)
   Saved to:           OUTPUT_PATH   [or: dry run — not written]
```

For each new suppression added, list:
```
  • [sup-YYYYMMDD-N] pattern: "<pattern>"
      scope:   <scope>
      category: <category>
      reason:  <reason>
```

If no dismissed threads were found, print:
```
ℹ️  No dismissed/resolved PRGenie threads found in PR #PR_NUMBER.
   Nothing to add to suppressions.json.
```
