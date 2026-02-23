"""Unit tests for the comment deduplicator."""

import pytest

from pr_reviewer.models import ReviewCategory, ReviewFinding, Severity
from pr_reviewer.output.deduplicator import Deduplicator
from pr_reviewer.output.formatter import format_inline_comment


def _make_finding(
    file: str = "src/foo.py",
    line: int = 10,
    message: str = "test issue",
    severity: Severity = Severity.MEDIUM,
) -> ReviewFinding:
    return ReviewFinding(
        file=file, line_start=line, line_end=line,
        severity=severity, category=ReviewCategory.BUG,
        message=message, confidence=0.8,
    )


class TestDeduplicator:
    def test_no_existing_comments(self):
        dedup = Deduplicator([])
        f = _make_finding()
        assert dedup.is_duplicate(f, format_inline_comment(f)) is False

    def test_fingerprint_match(self):
        f = _make_finding()
        body = format_inline_comment(f)
        existing = [{"path": f.file, "line": f.line_start, "body": body}]
        dedup = Deduplicator(existing)
        assert dedup.is_duplicate(f, body) is True

    def test_different_file_not_duplicate(self):
        f = _make_finding(file="src/bar.py")
        body = format_inline_comment(f)
        existing = [{"path": "src/foo.py", "line": f.line_start, "body": body}]
        dedup = Deduplicator(existing)
        assert dedup.is_duplicate(f, body) is False

    def test_semantic_match(self):
        message = (
            "SQL query constructed with string formatting — potential injection vulnerability. "
            "Use parameterized queries to prevent SQL injection attacks in database calls."
        )
        f = _make_finding(message=message)
        body = format_inline_comment(f)
        # Existing comment has the same semantic content at same location
        similar_body = message + " Please fix this critical issue."
        existing = [{"path": f.file, "line": f.line_start, "body": similar_body}]
        dedup = Deduplicator(existing)
        assert dedup.is_duplicate(f, body) is True

    def test_different_line_not_semantic_dup(self):
        message = (
            "SQL query constructed with string formatting — potential injection vulnerability. "
            "Use parameterized queries to prevent SQL injection attacks in database calls."
        )
        f = _make_finding(line=10, message=message)
        body = format_inline_comment(f)
        existing = [{"path": f.file, "line": 100, "body": message}]  # far away line
        dedup = Deduplicator(existing)
        # Line difference > 5, should NOT be a semantic dup
        assert dedup.is_duplicate(f, body) is False

    def test_filter_findings(self):
        findings = [_make_finding(line=i) for i in range(5)]
        existing = [
            {"path": "src/foo.py", "line": 0, "body": format_inline_comment(findings[0])},
            {"path": "src/foo.py", "line": 2, "body": format_inline_comment(findings[2])},
        ]
        dedup = Deduplicator(existing)
        unique = dedup.filter_findings(findings, format_inline_comment)
        # findings[0] and [2] are duplicates, so 3 remain
        assert len(unique) == 3
