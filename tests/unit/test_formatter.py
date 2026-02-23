"""Unit tests for the output formatter."""

import pytest

from pr_reviewer.models import PRSummary, ReviewFinding, ReviewCategory, Severity
from pr_reviewer.models import TestStub as PRTestStub
from pr_reviewer.output.formatter import format_inline_comment, format_summary_comment


class TestFormatInlineComment:
    def test_contains_severity(self, sample_finding):
        body = format_inline_comment(sample_finding)
        assert "HIGH" in body

    def test_contains_category(self, sample_finding):
        body = format_inline_comment(sample_finding)
        assert "SECURITY" in body

    def test_contains_message(self, sample_finding):
        body = format_inline_comment(sample_finding)
        assert "SQL query" in body

    def test_contains_suggestion(self, sample_finding):
        body = format_inline_comment(sample_finding)
        assert "parameterized" in body
        assert "```python" in body

    def test_contains_confidence(self, sample_finding):
        body = format_inline_comment(sample_finding)
        assert "92%" in body

    def test_no_suggestion_no_code_block(self):
        f = ReviewFinding(
            file="x.py", line_start=1, line_end=1,
            severity=Severity.LOW, category=ReviewCategory.STYLE,
            message="minor style issue", confidence=0.6
        )
        body = format_inline_comment(f)
        assert "```python" not in body

    def test_critical_emoji(self):
        f = ReviewFinding(
            file="x.py", line_start=1, line_end=1,
            severity=Severity.CRITICAL, category=ReviewCategory.SECURITY,
            message="critical issue", confidence=0.99
        )
        body = format_inline_comment(f)
        assert "🚨" in body


class TestFormatSummaryComment:
    def test_contains_risk_level(self):
        summary = PRSummary(
            overview="Good", intent="Fix", risk_level=Severity.HIGH,
            findings_by_severity={"HIGH": 2, "LOW": 1}
        )
        body = format_summary_comment(summary, [])
        assert "HIGH" in body

    def test_contains_overview(self):
        summary = PRSummary(
            overview="This PR adds payment processing.",
            intent="Stripe integration",
            risk_level=Severity.MEDIUM,
        )
        body = format_summary_comment(summary, [])
        assert "payment processing" in body

    def test_contains_findings_table(self):
        summary = PRSummary(
            overview="ok", intent="test", risk_level=Severity.LOW,
        )
        body = format_summary_comment(summary, [])
        assert "Severity" in body
        assert "Count" in body

    def test_contains_test_stubs(self):
        stub = PRTestStub(
            function_name="process_payment",
            test_name="test_process_payment",
            source_file="src/payment.py",
            stub_code="def test_process_payment():\n    assert True",
            description="Verify happy path",
        )
        summary = PRSummary(
            overview="ok", intent="test", risk_level=Severity.LOW,
            test_stubs=[stub],
        )
        body = format_summary_comment(summary, [])
        assert "test_process_payment" in body
        assert "```python" in body

    def test_contains_key_concerns(self):
        summary = PRSummary(
            overview="ok", intent="test", risk_level=Severity.MEDIUM,
            key_concerns=["Missing error handling", "No input validation"],
        )
        body = format_summary_comment(summary, [])
        assert "Missing error handling" in body

    def test_footer_present(self):
        summary = PRSummary(overview="ok", intent="", risk_level=Severity.INFO)
        body = format_summary_comment(summary, [])
        assert "pr-reviewer" in body
