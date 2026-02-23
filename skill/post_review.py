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
import io
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


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Parse a GitHub PR URL into (owner, repo, pr_number)."""
    m = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", url.rstrip("/"))
    if not m:
        sys.exit(f"ERROR: Could not parse PR URL: {url}\n"
                 "Expected format: https://github.com/OWNER/REPO/pull/NUMBER")
    return m.group(1), m.group(2), int(m.group(3))


def fetch_head_sha(gh: str, owner: str, repo: str, pr_number: int) -> str:
    """Fetch the head commit SHA for the PR."""
    result = subprocess.run(
        [gh, "pr", "view", f"https://github.com/{owner}/{repo}/pull/{pr_number}",
         "--json", "headRefOid", "--jq", ".headRefOid"],
        text=True, capture_output=True, encoding="utf-8"
    )
    if result.returncode != 0:
        sys.exit(f"ERROR: Could not fetch head SHA:\n{result.stderr}")
    return result.stdout.strip()


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


def post_inline_review(gh: str, owner: str, repo: str, pr_number: int,
                       head_sha: str, comments: list[dict], dry_run: bool) -> int:
    """Batch-post all inline comments as a single GitHub review. Returns count posted."""
    formatted = []
    for c in comments:
        formatted.append({
            "path": c["path"],
            "line": c["line"],
            "side": "RIGHT",
            "body": format_comment_body(c),
        })

    if dry_run:
        print(f"\n[DRY RUN] Would post {len(formatted)} inline comments:")
        for f in formatted:
            print(f"  {f['path']}:{f['line']} — {f['body'][:80].splitlines()[0]}")
        return len(formatted)

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
        text=True, capture_output=True, encoding="utf-8"
    )

    if result.returncode != 0:
        print(f"❌ Failed to post inline review:\n{result.stderr[:2000]}", file=sys.stderr)
        return 0

    data = json.loads(result.stdout)
    count = len(data.get("comments", []))
    print(f"✅ Inline review posted — review ID: {data.get('id')}, comments: {count}")
    return count


def post_summary(gh: str, pr_url: str, summary: str, dry_run: bool) -> bool:
    """Post the top-level summary comment. Returns True on success."""
    if dry_run:
        print("\n[DRY RUN] Would post summary comment:")
        print(summary[:300] + ("..." if len(summary) > 300 else ""))
        return True

    result = subprocess.run(
        [gh, "pr", "comment", pr_url, "--body", summary],
        text=True, capture_output=True, encoding="utf-8"
    )

    if result.returncode != 0:
        print(f"❌ Failed to post summary:\n{result.stderr[:1000]}", file=sys.stderr)
        return False

    print(f"✅ Summary comment posted: {result.stdout.strip()}")
    return True


def main():
    parser = argparse.ArgumentParser(
        description="Post a code review (inline comments + summary) to a GitHub PR."
    )
    parser.add_argument("--pr-url", required=True, help="Full GitHub PR URL")
    parser.add_argument("--comments", required=True, help="Path to comments JSON file")
    parser.add_argument("--summary", required=True, help="Path to summary Markdown file")
    parser.add_argument("--gh-path", default="gh", help="Path to the gh CLI binary")
    parser.add_argument("--head-sha", default="", help="Head commit SHA (fetched if omitted)")
    parser.add_argument("--dry-run", action="store_true", help="Print without posting")
    args = parser.parse_args()

    # Resolve gh binary
    gh = args.gh_path

    # Parse PR URL
    owner, repo, pr_number = parse_pr_url(args.pr_url)
    print(f"PR: {owner}/{repo}#{pr_number}")

    # Fetch HEAD SHA if not supplied
    head_sha = args.head_sha or fetch_head_sha(gh, owner, repo, pr_number)
    print(f"HEAD SHA: {head_sha[:12]}...")

    # Load comments
    comments_path = Path(args.comments)
    if not comments_path.exists():
        sys.exit(f"ERROR: Comments file not found: {comments_path}")
    comments = json.loads(comments_path.read_text(encoding="utf-8"))
    print(f"Loaded {len(comments)} comments from {comments_path}")

    # Load summary
    summary_path = Path(args.summary)
    if not summary_path.exists():
        sys.exit(f"ERROR: Summary file not found: {summary_path}")
    summary = summary_path.read_text(encoding="utf-8")

    # Post
    inline_count = post_inline_review(gh, owner, repo, pr_number, head_sha, comments, args.dry_run)
    summary_ok = post_summary(gh, args.pr_url, summary, args.dry_run)

    mode = "[dry run]" if args.dry_run else "posted"
    print(f"\n{'[DRY RUN] ' if args.dry_run else ''}✅ PR Review Complete")
    print(f"   PR:              {owner}/{repo}#{pr_number}")
    print(f"   Inline comments: {inline_count} {mode}")
    print(f"   Summary:         {'ok' if summary_ok else 'FAILED'} {mode}")

    if not args.dry_run and (inline_count == 0 or not summary_ok):
        sys.exit(1)


if __name__ == "__main__":
    main()
