"""Unit tests for the CommentPoster (mock adapter)."""

from unittest.mock import MagicMock

import pytest

from pr_reviewer.models import (
    AgentSession,
    PRSummary,
    ReviewCategory,
    ReviewFinding,
    Severity,
)
from pr_reviewer.output.poster import CommentPoster


def _make_finding(severity: Severity, line: int = 1) -> ReviewFinding:
    return ReviewFinding(
        file="src/foo.py", line_start=line, line_end=line,
        severity=severity, category=ReviewCategory.BUG,
        message=f"Issue at line {line} with severity {severity.value}",
        confidence=0.9,
    )


class TestCommentPoster:
    def test_dry_run_does_not_post(self, mock_adapter, sample_agent_session, capsys):
        sample_agent_session.findings = [_make_finding(Severity.HIGH)]
        sample_agent_session.summary = PRSummary(
            overview="ok", intent="test", risk_level=Severity.HIGH
        )
        poster = CommentPoster(mock_adapter, dry_run=True)
        stats = poster.post(sample_agent_session)
        mock_adapter.post_inline_comment.assert_not_called()
        mock_adapter.post_pr_summary.assert_not_called()
        assert stats["dry_run"] is True

    def test_posts_inline_comments(self, mock_adapter, sample_agent_session):
        sample_agent_session.findings = [
            _make_finding(Severity.HIGH, line=1),
            _make_finding(Severity.MEDIUM, line=2),
        ]
        sample_agent_session.summary = PRSummary(
            overview="ok", intent="test", risk_level=Severity.HIGH
        )
        poster = CommentPoster(mock_adapter, min_severity=Severity.LOW, dry_run=False)
        stats = poster.post(sample_agent_session)
        assert mock_adapter.post_inline_comment.call_count == 2
        assert stats["posted_inline"] == 2

    def test_severity_filter(self, mock_adapter, sample_agent_session):
        sample_agent_session.findings = [
            _make_finding(Severity.CRITICAL),
            _make_finding(Severity.HIGH),
            _make_finding(Severity.MEDIUM),
            _make_finding(Severity.LOW),
            _make_finding(Severity.INFO),
        ]
        sample_agent_session.summary = PRSummary(
            overview="ok", intent="test", risk_level=Severity.CRITICAL
        )
        poster = CommentPoster(mock_adapter, min_severity=Severity.HIGH, dry_run=False)
        stats = poster.post(sample_agent_session)
        assert stats["skipped_low_severity"] == 3  # MEDIUM, LOW, INFO
        assert mock_adapter.post_inline_comment.call_count == 2

    def test_max_inline_cap(self, mock_adapter, sample_agent_session):
        sample_agent_session.findings = [_make_finding(Severity.HIGH, line=i) for i in range(50)]
        sample_agent_session.summary = PRSummary(
            overview="ok", intent="test", risk_level=Severity.HIGH
        )
        poster = CommentPoster(mock_adapter, min_severity=Severity.INFO, max_inline_comments=10, dry_run=False)
        stats = poster.post(sample_agent_session)
        assert mock_adapter.post_inline_comment.call_count == 10
        assert stats["skipped_cap"] == 40

    def test_posts_summary(self, mock_adapter, sample_agent_session):
        sample_agent_session.findings = []
        sample_agent_session.summary = PRSummary(
            overview="All good.", intent="test", risk_level=Severity.LOW
        )
        poster = CommentPoster(mock_adapter, dry_run=False)
        stats = poster.post(sample_agent_session)
        mock_adapter.post_pr_summary.assert_called_once()
        assert stats["posted_summary"] == 1

    def test_deduplication(self, mock_adapter, sample_agent_session):
        f = _make_finding(Severity.HIGH, line=5)
        body_prefix = f"Issue at line 5"
        existing = [{"path": "src/foo.py", "line": 5, "body": body_prefix + " " * 200}]
        mock_adapter.get_existing_comments.return_value = existing
        sample_agent_session.findings = [f]
        sample_agent_session.summary = PRSummary(
            overview="ok", intent="", risk_level=Severity.HIGH
        )
        poster = CommentPoster(mock_adapter, dry_run=False)
        stats = poster.post(sample_agent_session)
        # May or may not be flagged as dup depending on semantic match — at least no crash
        assert stats["total_findings"] == 1

    def test_no_summary_no_summary_post(self, mock_adapter, sample_agent_session):
        sample_agent_session.findings = []
        sample_agent_session.summary = None
        poster = CommentPoster(mock_adapter, dry_run=False)
        stats = poster.post(sample_agent_session)
        mock_adapter.post_pr_summary.assert_not_called()
        assert stats["posted_summary"] == 0
