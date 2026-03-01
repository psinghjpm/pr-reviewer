#!/usr/bin/env python3
"""JIRA validator adapter for pr-reviewer context protocol.

Checks that the PR title or body references a valid JIRA ticket, and that
the ticket is in an appropriate state (not already closed before the PR merges).

Writes results to .pr-reviewer/context/jira.json.

Usage:
    JIRA_TOKEN=... JIRA_USER=me@example.com \
        python .pr-reviewer/adapters/jira.py \
        --pr-url https://github.com/owner/repo/pull/123 \
        --jira-url https://myorg.atlassian.net

Environment variables:
    JIRA_TOKEN   Atlassian API token (required)
    JIRA_USER    Atlassian account email (required)
    JIRA_URL     Jira base URL — overridden by --jira-url
"""

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path

_TICKET_RE = re.compile(r"\b([A-Z][A-Z0-9]+-[0-9]+)\b")

# Status names (lower-cased) that mean work is done / should not be in-flight
_CLOSED_STATUSES = frozenset({
    "done", "closed", "resolved", "won't do", "wont do",
    "duplicate", "invalid", "cancelled", "canceled",
})


def _jira_get(base_url: str, user: str, token: str, path: str) -> dict:
    url = f"{base_url.rstrip('/')}{path}"
    auth = b64encode(f"{user}:{token}".encode()).decode()
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Basic {auth}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _pr_text(pr_url: str) -> str:
    result = subprocess.run(
        ["gh", "pr", "view", pr_url, "--json", "title,body"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return ""
    data = json.loads(result.stdout)
    return f"{data.get('title', '')} {data.get('body', '')}"


def _check_ticket(base_url: str, user: str, token: str, ticket: str) -> dict:
    try:
        data = _jira_get(base_url, user, token, f"/rest/api/3/issue/{ticket}")
    except Exception as exc:
        return {"found": False, "ticket": ticket, "error": str(exc)}

    fields = data.get("fields", {})
    status = fields.get("status", {}).get("name", "").lower()
    return {
        "found":     True,
        "ticket":    ticket,
        "summary":   fields.get("summary", ""),
        "type":      fields.get("issuetype", {}).get("name", ""),
        "status":    status,
        "assignee":  (fields.get("assignee") or {}).get("displayName", "unassigned"),
        "is_closed": status in _CLOSED_STATUSES,
        "url":       f"{base_url.rstrip('/')}/browse/{ticket}",
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate JIRA ticket reference in a PR and write pr-reviewer context schema"
    )
    parser.add_argument("--pr-url", required=True, help="GitHub PR URL")
    parser.add_argument("--jira-url", default=os.getenv("JIRA_URL", ""),
                        help="Jira base URL (e.g. https://myorg.atlassian.net)")
    parser.add_argument("--output", "-o", default=".pr-reviewer/context/jira.json")
    args = parser.parse_args()

    token = os.getenv("JIRA_TOKEN", "")
    user  = os.getenv("JIRA_USER",  "")
    if not args.jira_url:
        print("❌ --jira-url or JIRA_URL is required", file=sys.stderr)
        sys.exit(1)
    if not token or not user:
        print("❌ JIRA_TOKEN and JIRA_USER environment variables are required", file=sys.stderr)
        sys.exit(1)

    print(f"⏳ jira: reading PR text from {args.pr_url}…", file=sys.stderr)
    pr_text = _pr_text(args.pr_url)
    # deduplicate while preserving order
    tickets = list(dict.fromkeys(_TICKET_RE.findall(pr_text)))

    findings: list[dict] = []
    ticket_details: list[dict] = []

    if not tickets:
        findings.append({
            "file":     "",
            "line":     1,
            "severity": "LOW",
            "category": "STYLE",
            "message":  (
                "PR title or description does not reference a JIRA ticket. "
                "Expected pattern: PROJECT-123 (e.g. MYPROJ-456)."
            ),
            "rule_id":  "JIRA-001",
            "url":      args.jira_url,
        })
    else:
        for ticket in tickets:
            print(f"⏳ jira: checking {ticket}…", file=sys.stderr)
            info = _check_ticket(args.jira_url, user, token, ticket)
            ticket_details.append(info)

            if not info["found"]:
                findings.append({
                    "file":     "",
                    "line":     1,
                    "severity": "MEDIUM",
                    "category": "STYLE",
                    "message":  (
                        f"JIRA ticket {ticket} referenced in PR could not be found "
                        f"({info.get('error', 'not found')})."
                    ),
                    "rule_id":  "JIRA-002",
                    "url":      args.jira_url,
                })
            elif info["is_closed"]:
                findings.append({
                    "file":     "",
                    "line":     1,
                    "severity": "MEDIUM",
                    "category": "STYLE",
                    "message":  (
                        f"JIRA ticket {ticket} ({info['summary']!r}) has status "
                        f"'{info['status']}' — this work item appears closed "
                        f"before the PR was merged."
                    ),
                    "rule_id":  "JIRA-003",
                    "url":      info["url"],
                })

    passed = len(findings) == 0
    output = {
        "source":       "jira",
        "version":      "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "findings":     findings,
        "summary": {
            "label":           "OK" if passed else "FAIL",
            "passed":          passed,
            "tickets_found":   tickets,
            "tickets_checked": len(ticket_details),
        },
        "metadata": {
            "pr_url":         args.pr_url,
            "jira_url":       args.jira_url,
            "ticket_details": ticket_details,
        },
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, indent=2))
    icon = "✅" if passed else "⚠️"
    print(
        f"{icon} jira: tickets={tickets or 'none'} | {len(findings)} findings → {out}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
