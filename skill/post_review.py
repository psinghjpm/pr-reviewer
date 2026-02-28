#!/usr/bin/env python3
"""
post_review.py — Generic GitHub PR review poster.

Usage:
    python post_review.py --pr-url URL --comments comments.json --summary summary.md [--gh-path PATH]

Arguments:
    --pr-url      Full GitHub PR URL (e.g. https://github.com/owner/repo/pull/42)
    --comments    Path to a JSON file containing the comments array (see schema below)
    --summary     Path to a Markdown file containing the top-level summary comment
    --gh-path     Path to the gh CLI binary (default: gh)
    --head-sha    Commit SHA to attach the review to (fetched from gh if omitted)
    --dry-run     Print what would be posted without calling any APIs

API call budget (regardless of comment count):
    1. gh pr view  — fetch head SHA (skipped if --head-sha supplied)
    2. gh pr diff  — fetch unified diff to compute per-line positions
    3. POST /pulls/{pr}/reviews — post ALL inline comments in one batch review
    4. gh pr comment — post the summary as a top-level PR comment

Comments JSON schema (array of objects):
    [
      {
        "path":     "packages/foo/bar.ts",   // repo-relative file path
        "line":     42,                       // line number in the NEW file
        "severity": "HIGH",                   // CRITICAL|HIGH|MEDIUM|LOW|INFO
        "category": "SECURITY",               // BUG|LOGIC|SECURITY|PERFORMANCE|MAINTAINABILITY|MISSING_TEST|STYLE
        "message":  "Description of issue",
        "suggestion_lang": "typescript",      // language for the code block
        "suggestion": "// fixed code here",  // code suggestion (optional)
        "suggestion_text": "Plain text suggestion when no code block is needed",  // (optional)
        "confidence": 95                      // integer 0-100
      }
    ]
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# Force UTF-8 output on Windows (cp1252 default can't render emoji)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SEVERITY_EMOJI = {
    "CRITICAL": "🚨",
    "HIGH": "🔴",
    "MEDIUM": "🟡",
    "LOW": "🔵",
    "INFO": "ℹ️",
}


# ---------------------------------------------------------------------------
# URL / metadata helpers
# ---------------------------------------------------------------------------

def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse a GitHub PR URL into (owner, repo, pr_number)."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url.rstrip("/"))
    if not m:
        sys.exit(f"ERROR: Could not parse PR URL: {url}\n"
                 "Expected format: https://github.com/OWNER/REPO/pull/NUMBER")
    return m.group(1), m.group(2), int(m.group(3))


def fetch_pr_info(gh: str, owner: str, repo: str, pr_number: int) -> tuple[str, str]:
    """Fetch (head_sha, diff_text) in two calls."""
    sha_result = subprocess.run(
        [gh, "pr", "view", f"https://github.com/{owner}/{repo}/pull/{pr_number}",
         "--json", "headRefOid", "--jq", ".headRefOid"],
        text=True, capture_output=True, encoding="utf-8",
    )
    if sha_result.returncode != 0:
        sys.exit(f"ERROR: Could not fetch head SHA:\n{sha_result.stderr}")
    head_sha = sha_result.stdout.strip()

    diff_result = subprocess.run(
        [gh, "pr", "diff", f"https://github.com/{owner}/{repo}/pull/{pr_number}"],
        text=True, capture_output=True, encoding="utf-8",
    )
    if diff_result.returncode != 0:
        sys.exit(f"ERROR: Could not fetch PR diff:\n{diff_result.stderr}")

    return head_sha, diff_result.stdout


# ---------------------------------------------------------------------------
# Diff position map
# ---------------------------------------------------------------------------

def build_position_map(diff_text: str) -> dict[tuple[str, int], int]:
    """
    Parse a unified diff and return {(file_path, new_line_number): diff_position}.

    GitHub's batch review API requires `position` — the 1-based line index within
    each file's diff section, counting from the first @@ hunk header (inclusive).
    Every hunk header, context line, added line, and deleted line consumes one
    position. The count resets at each new "diff --git" file header.

    Lines starting with backslash ("no newline at end of file" markers) are skipped
    — they don't consume a position in the GitHub UI.
    """
    positions: dict[tuple[str, int], int] = {}
    current_file: str | None = None
    position = 0
    new_line = 0

    for raw in diff_text.splitlines():
        # ── File header ──────────────────────────────────────────────────────
        if raw.startswith("diff --git "):
            current_file = None
            position = 0
            continue
        if raw.startswith("+++ b/"):
            current_file = raw[6:].rstrip()
            position = 0
            continue
        if raw.startswith("--- ") or raw.startswith("index ") \
                or raw.startswith("new file") or raw.startswith("deleted file") \
                or raw.startswith("Binary "):
            continue

        if current_file is None:
            continue

        # ── Hunk header ──────────────────────────────────────────────────────
        m = re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", raw)
        if m:
            position += 1          # hunk header itself is a position
            new_line = int(m.group(1))
            continue

        # ── "No newline" marker — skip, doesn't consume a position ───────────
        if raw.startswith("\\"):
            continue

        # ── Diff content lines ───────────────────────────────────────────────
        position += 1
        if raw.startswith("+"):
            positions[(current_file, new_line)] = position
            new_line += 1
        elif raw.startswith("-"):
            pass                   # deleted — old file only, no new_line advance
        else:
            # context line (leading space)
            positions[(current_file, new_line)] = position
            new_line += 1

    return positions


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------

def format_comment_body(c: dict) -> str:
    """Format a single finding dict into a GitHub review comment body."""
    emoji = SEVERITY_EMOJI.get(c["severity"], "ℹ️")
    header = f"{emoji} **{c['severity']}** [{c['category']}]"
    message = c["message"]

    suggestion_block = ""
    if c.get("suggestion"):
        lang = c.get("suggestion_lang", "")
        suggestion_block = f"\n\n**Suggestion:**\n```{lang}\n{c['suggestion']}\n```"
    elif c.get("suggestion_text"):
        suggestion_block = f"\n\n**Suggestion:** {c['suggestion_text']}"

    confidence = c.get("confidence", "")
    footer = f"\n\n<sub>Confidence: {confidence}% | 🤖 pr-reviewer (Claude Code)</sub>"

    return f"{header}\n\n{message}{suggestion_block}{footer}"


# ---------------------------------------------------------------------------
# Posting
# ---------------------------------------------------------------------------

def post_inline_review(
    gh: str,
    owner: str,
    repo: str,
    pr_number: int,
    head_sha: str,
    comments: list[dict],
    position_map: dict[tuple[str, int], int],
    dry_run: bool,
) -> int:
    """
    Post all inline comments as a SINGLE GitHub review (one API call).

    Each comment's (path, line) is resolved to a diff `position` using the
    pre-built position_map. Comments that fall outside the diff are skipped
    with a warning rather than aborting the whole review.
    """
    formatted = []
    skipped = []

    for c in comments:
        key = (c["path"], int(c["line"]))
        pos = position_map.get(key)
        if pos is None:
            skipped.append(c)
            continue
        formatted.append({
            "path": c["path"],
            "position": pos,
            "body": format_comment_body(c),
        })

    if skipped:
        paths = ", ".join(f"{s['path']}:{s['line']}" for s in skipped)
        print(f"  ⚠️  {len(skipped)} comment(s) not in diff (skipped): {paths}")

    if dry_run:
        print(f"\n[DRY RUN] Would post {len(formatted)} inline comment(s) in 1 review call:")
        for f in formatted:
            print(f"  pos={f['position']:3}  {f['path']} — {f['body'][:80].splitlines()[0]}")
        return len(formatted)

    if not formatted:
        print("  ℹ️  No in-diff comments to post.")
        return 0

    payload = {
        "commit_id": head_sha,
        "body": "",
        "event": "COMMENT",
        "comments": formatted,
    }

    result = subprocess.run(
        [gh, "api", f"repos/{owner}/{repo}/pulls/{pr_number}/reviews",
         "--method", "POST", "--input", "-"],
        input=json.dumps(payload),
        text=True, capture_output=True, encoding="utf-8",
    )

    if result.returncode != 0:
        print(f"❌ Failed to post inline review:\n{result.stderr[:2000]}", file=sys.stderr)
        return 0

    # The creation endpoint returns the review object, NOT the comments array.
    # Trust that all submitted comments landed; verify via review ID if needed.
    data = json.loads(result.stdout)
    review_id = data.get("id")
    count = len(formatted)
    print(f"✅ Inline review posted — review ID: {review_id}, comments: {count}")
    return count


def post_summary(gh: str, pr_url: str, summary: str, dry_run: bool) -> bool:
    """Post the top-level summary as a PR comment (one API call). Returns True on success."""
    if dry_run:
        print("\n[DRY RUN] Would post summary comment:")
        print(summary[:300] + ("..." if len(summary) > 300 else ""))
        return True

    result = subprocess.run(
        [gh, "pr", "comment", pr_url, "--body", summary],
        text=True, capture_output=True, encoding="utf-8",
    )

    if result.returncode != 0:
        print(f"❌ Failed to post summary:\n{result.stderr[:1000]}", file=sys.stderr)
        return False

    print(f"✅ Summary comment posted: {result.stdout.strip()}")
    return True


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Post a code review (inline comments + summary) to a GitHub PR."
    )
    parser.add_argument("--pr-url",   required=True, help="Full GitHub PR URL")
    parser.add_argument("--comments", required=True, help="Path to comments JSON file")
    parser.add_argument("--summary",  required=True, help="Path to summary Markdown file")
    parser.add_argument("--gh-path",  default="gh",  help="Path to the gh CLI binary")
    parser.add_argument("--head-sha", default="",    help="Head commit SHA (fetched if omitted)")
    parser.add_argument("--dry-run",  action="store_true", help="Print without posting")
    args = parser.parse_args()

    gh = args.gh_path
    owner, repo, pr_number = parse_pr_url(args.pr_url)
    print(f"PR: {owner}/{repo}#{pr_number}")

    # ── Fetch metadata + diff (1–2 API calls) ───────────────────────────────
    if args.head_sha:
        head_sha = args.head_sha
        diff_result = subprocess.run(
            [gh, "pr", "diff", args.pr_url],
            text=True, capture_output=True, encoding="utf-8",
        )
        if diff_result.returncode != 0:
            sys.exit(f"ERROR: Could not fetch PR diff:\n{diff_result.stderr}")
        diff_text = diff_result.stdout
    else:
        head_sha, diff_text = fetch_pr_info(gh, owner, repo, pr_number)

    print(f"HEAD SHA: {head_sha[:12]}...")

    # ── Build position map from diff (no API call) ───────────────────────────
    position_map = build_position_map(diff_text)
    print(f"Diff positions mapped: {len(position_map)} lines across "
          f"{len({p for p, _ in position_map})} file(s)")

    # ── Load inputs ──────────────────────────────────────────────────────────
    comments_path = Path(args.comments)
    if not comments_path.exists():
        sys.exit(f"ERROR: Comments file not found: {comments_path}")
    comments = json.loads(comments_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(comments)} comment(s) from {comments_path}")

    summary_path = Path(args.summary)
    if not summary_path.exists():
        sys.exit(f"ERROR: Summary file not found: {summary_path}")
    summary = summary_path.read_text(encoding="utf-8")

    # ── Post: summary first, then single-batch inline review ─────────────────
    summary_ok    = post_summary(gh, args.pr_url, summary, args.dry_run)
    inline_count  = post_inline_review(
        gh, owner, repo, pr_number, head_sha, comments, position_map, args.dry_run
    )

    # ── Report ───────────────────────────────────────────────────────────────
    mode = "[dry run]" if args.dry_run else "posted"
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}✅ PR Review Complete")
    print(f"   PR:              {owner}/{repo}#{pr_number}")
    print(f"   Inline comments: {inline_count} {mode}")
    print(f"   Summary:         {'ok' if summary_ok else 'FAILED'} {mode}")

    if not args.dry_run and (inline_count == 0 or not summary_ok):
        sys.exit(1)


if __name__ == "__main__":
    main()
