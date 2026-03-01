#!/usr/bin/env python3
"""SonarQube / SonarCloud adapter for pr-reviewer context protocol.

Pulls quality gate status + open issues via the SonarQube REST API and
writes them to .pr-reviewer/context/sonar.json.

Usage:
    SONAR_TOKEN=... python .pr-reviewer/adapters/sonar.py \
        --url https://sonarcloud.io \
        --project my-org_my-repo

Environment variables:
    SONAR_TOKEN    SonarQube/SonarCloud user token (required)
    SONAR_URL      Base URL — overridden by --url (default: https://sonarcloud.io)
    SONAR_PROJECT  Project key — overridden by --project
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from base64 import b64encode
from datetime import datetime, timezone
from pathlib import Path

_SEVERITY_MAP = {
    "BLOCKER":  "CRITICAL",
    "CRITICAL": "HIGH",
    "MAJOR":    "MEDIUM",
    "MINOR":    "LOW",
    "INFO":     "INFO",
}

_TYPE_MAP = {
    "BUG":              "BUG",
    "VULNERABILITY":    "SECURITY",
    "CODE_SMELL":       "MAINTAINABILITY",
    "SECURITY_HOTSPOT": "SECURITY",
}


def _get(base_url: str, token: str, path: str, params: dict = {}) -> dict:
    url = f"{base_url.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
    auth = b64encode(f"{token}:".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def _quality_gate(base_url: str, token: str, project: str) -> dict:
    data = _get(base_url, token, "/api/qualitygates/project_status",
                {"projectKey": project})
    return data.get("projectStatus", {})


def _issues(base_url: str, token: str, project: str) -> list[dict]:
    all_issues, page = [], 1
    while True:
        data = _get(base_url, token, "/api/issues/search", {
            "componentKeys": project,
            "statuses":      "OPEN,CONFIRMED,REOPENED",
            "severities":    "BLOCKER,CRITICAL,MAJOR,MINOR",
            "ps": 100,
            "p":  page,
        })
        batch = data.get("issues", [])
        all_issues.extend(batch)
        if len(all_issues) >= data.get("total", 0) or not batch:
            break
        page += 1
    return all_issues


def _to_finding(issue: dict, base_url: str, project: str) -> dict:
    component = issue.get("component", "")
    file_path = component.replace(f"{project}:", "", 1)
    line = issue.get("line") or issue.get("textRange", {}).get("startLine", 1)
    return {
        "file":     file_path,
        "line":     line,
        "severity": _SEVERITY_MAP.get(issue.get("severity", "MAJOR"), "MEDIUM"),
        "category": _TYPE_MAP.get(issue.get("type", "CODE_SMELL"), "MAINTAINABILITY"),
        "message":  issue.get("message", ""),
        "rule_id":  issue.get("rule", ""),
        "url": (
            f"{base_url.rstrip('/')}/project/issues"
            f"?id={project}&open={issue.get('key', '')}"
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Pull SonarQube quality gate + issues into pr-reviewer context schema"
    )
    parser.add_argument("--url", default=os.getenv("SONAR_URL", "https://sonarcloud.io"),
                        help="SonarQube/SonarCloud base URL")
    parser.add_argument("--project", default=os.getenv("SONAR_PROJECT", ""),
                        help="SonarQube project key")
    parser.add_argument("--output", "-o", default=".pr-reviewer/context/sonar.json")
    args = parser.parse_args()

    token = os.getenv("SONAR_TOKEN", "")
    if not token:
        print("❌ SONAR_TOKEN is required", file=sys.stderr)
        sys.exit(1)
    if not args.project:
        print("❌ --project or SONAR_PROJECT is required", file=sys.stderr)
        sys.exit(1)

    print(f"⏳ sonar: fetching quality gate for {args.project}…", file=sys.stderr)
    gate = _quality_gate(args.url, token, args.project)

    print("⏳ sonar: fetching open issues…", file=sys.stderr)
    findings = [_to_finding(i, args.url, args.project)
                for i in _issues(args.url, token, args.project)]

    counts: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1

    gate_status = gate.get("status", "NONE")   # OK | WARN | ERROR | NONE
    output = {
        "source": "sonar",
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "findings": findings,
        "summary": {
            "label":    gate_status,
            "passed":   gate_status == "OK",
            "critical": counts["CRITICAL"],
            "high":     counts["HIGH"],
            "medium":   counts["MEDIUM"],
            "low":      counts["LOW"],
        },
        "metadata": {
            "project":          args.project,
            "sonar_url":        args.url,
            "gate_conditions":  gate.get("conditions", []),
        },
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, indent=2))
    icon = "✅" if gate_status == "OK" else "❌"
    print(f"{icon} sonar: gate={gate_status} | {len(findings)} issues → {out}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
