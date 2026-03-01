#!/usr/bin/env python3
"""Snyk Code adapter for pr-reviewer context protocol.

Translates `snyk code test --json` output into the pr-reviewer
context provider schema and writes it to .pr-reviewer/context/snyk.json.

Usage:
    # Run Snyk and pipe output directly:
    snyk code test --json | python .pr-reviewer/adapters/snyk.py

    # Read from a saved file (useful in CI where Snyk runs separately):
    python .pr-reviewer/adapters/snyk.py --input /tmp/snyk.json

    # Custom output path:
    python .pr-reviewer/adapters/snyk.py --output .pr-reviewer/context/snyk.json

Environment variables: none required (Snyk auth handled by the Snyk CLI).
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Snyk SARIF level → pr-reviewer severity
_LEVEL_MAP = {
    "error":   "HIGH",
    "warning": "MEDIUM",
    "note":    "LOW",
    "none":    "INFO",
}

# Legacy Snyk severity field → pr-reviewer severity
_LEGACY_SEVERITY_MAP = {
    "critical": "CRITICAL",
    "high":     "HIGH",
    "medium":   "MEDIUM",
    "low":      "LOW",
}


def _parse_sarif(data: dict) -> list[dict]:
    """Parse SARIF-format output from `snyk code test --json`."""
    findings = []
    for run in data.get("runs", []):
        rules = {
            r["id"]: r
            for r in run.get("tool", {}).get("driver", {}).get("rules", [])
        }
        for result in run.get("results", []):
            rule_id = result.get("ruleId", "")
            severity = _LEVEL_MAP.get(result.get("level", "warning"), "MEDIUM")
            message = result.get("message", {}).get("text", "")
            rule = rules.get(rule_id, {})
            url = rule.get("helpUri") or rule.get("help", {}).get("markdown", "") or ""

            for loc in result.get("locations", []):
                phys = loc.get("physicalLocation", {})
                file_path = phys.get("artifactLocation", {}).get("uri", "").lstrip("/")
                line = phys.get("region", {}).get("startLine", 1)
                findings.append({
                    "file": file_path,
                    "line": line,
                    "severity": severity,
                    "category": "SECURITY",
                    "message": message,
                    "rule_id": rule_id,
                    "url": url,
                })
    return findings


def _parse_legacy(data: dict) -> list[dict]:
    """Parse older non-SARIF Snyk output (snyk test --json on packages)."""
    findings = []
    for vuln in data.get("vulnerabilities", []):
        severity = _LEGACY_SEVERITY_MAP.get(
            vuln.get("severity", "medium").lower(), "MEDIUM"
        )
        pkg = vuln.get("packageName", "unknown")
        ver = vuln.get("version", "?")
        findings.append({
            "file": vuln.get("from", [pkg])[-1],
            "line": 1,
            "severity": severity,
            "category": "SECURITY",
            "message": f"{vuln.get('title', 'Vulnerability')} in {pkg}@{ver}",
            "rule_id": vuln.get("id", ""),
            "url": vuln.get("url", ""),
        })
    return findings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Translate `snyk code test --json` output to pr-reviewer context schema"
    )
    parser.add_argument("--input", "-i", help="Path to Snyk JSON file (default: stdin)")
    parser.add_argument(
        "--output", "-o",
        default=".pr-reviewer/context/snyk.json",
        help="Output path (default: .pr-reviewer/context/snyk.json)",
    )
    args = parser.parse_args()

    src = open(args.input) if args.input else sys.stdin
    with src:
        data = json.load(src)

    findings = _parse_sarif(data) if "runs" in data else _parse_legacy(data)

    counts: dict[str, int] = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1

    output = {
        "source": "snyk",
        "version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "findings": findings,
        "summary": {
            "passed": data.get("ok", len(findings) == 0),
            "critical": counts["CRITICAL"],
            "high":     counts["HIGH"],
            "medium":   counts["MEDIUM"],
            "low":      counts["LOW"],
        },
        "metadata": {
            "unique_issues": data.get("uniqueCount", len(findings)),
        },
    }

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(output, indent=2))
    print(f"✅ snyk: {len(findings)} findings → {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
