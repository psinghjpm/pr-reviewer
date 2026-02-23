"""Unit tests for Pydantic models."""

import pytest

from pr_reviewer.models import (
    AgentSession,
    DiffStatus,
    FileDiff,
    Hunk,
    HunkLine,
    Platform,
    PRMetadata,
    ReviewCategory,
    ReviewFinding,
    Severity,
    PRSummary,
    DependencyNode,
    SymbolInfo,
    CallSite,
)
from pr_reviewer.models import TestStub as PRTestStub


class TestSeverityOrdering:
    def test_less_than(self):
        assert Severity.INFO < Severity.LOW
        assert Severity.LOW < Severity.MEDIUM
        assert Severity.MEDIUM < Severity.HIGH
        assert Severity.HIGH < Severity.CRITICAL

    def test_greater_than(self):
        assert Severity.CRITICAL > Severity.HIGH
        assert Severity.MEDIUM > Severity.LOW

    def test_equal(self):
        assert Severity.HIGH == Severity.HIGH

    def test_ge(self):
        assert Severity.HIGH >= Severity.HIGH
        assert Severity.CRITICAL >= Severity.LOW

    def test_le(self):
        assert Severity.LOW <= Severity.LOW
        assert Severity.INFO <= Severity.CRITICAL


class TestFileDiff:
    def test_changed_line_numbers(self):
        hunk = Hunk(
            source_start=1, source_length=2, target_start=1, target_length=3,
            lines=[
                HunkLine(line_type=" ", value="unchanged\n", source_line_no=1, target_line_no=1),
                HunkLine(line_type="+", value="added\n", target_line_no=2),
                HunkLine(line_type="+", value="also added\n", target_line_no=3),
                HunkLine(line_type="-", value="removed\n", source_line_no=2),
            ]
        )
        fd = FileDiff(path="foo.py", status=DiffStatus.MODIFIED, hunks=[hunk])
        assert fd.changed_line_numbers == [2, 3]

    def test_status_values(self):
        for status in DiffStatus:
            fd = FileDiff(path="x.py", status=status)
            assert fd.status == status


class TestReviewFinding:
    def test_fingerprint(self):
        f = ReviewFinding(
            file="src/foo.py", line_start=10, line_end=10,
            severity=Severity.HIGH, category=ReviewCategory.BUG,
            message="A" * 100, confidence=0.9
        )
        fp = f.fingerprint
        assert "src/foo.py" in fp
        assert ":10:" in fp
        assert len(fp.split(":")[-1]) <= 80

    def test_confidence_bounds(self):
        with pytest.raises(Exception):
            ReviewFinding(
                file="x.py", line_start=1, line_end=1,
                severity=Severity.LOW, category=ReviewCategory.STYLE,
                message="msg", confidence=1.5  # invalid
            )

    def test_defaults(self):
        f = ReviewFinding(
            file="x.py", line_start=1, line_end=1,
            severity=Severity.INFO, category=ReviewCategory.STYLE,
            message="msg"
        )
        assert f.suggestion == ""
        assert f.confidence == 0.8


class TestPRMetadata:
    def test_round_trip(self, sample_pr_metadata):
        data = sample_pr_metadata.model_dump()
        restored = PRMetadata(**data)
        assert restored.pr_id == sample_pr_metadata.pr_id
        assert restored.platform == Platform.GITHUB

    def test_is_draft_default(self):
        pr = PRMetadata(
            pr_id=1, title="t", author="a",
            source_branch="feature", target_branch="main",
            base_sha="a", head_sha="b",
            platform=Platform.BITBUCKET,
            repo_full_name="ws/repo",
            url="https://bitbucket.org/ws/repo/pull-requests/1",
        )
        assert pr.is_draft is False


class TestAgentSession:
    def test_initial_state(self, sample_agent_session):
        s = sample_agent_session
        assert s.tool_call_count == 0
        assert s.max_tool_calls == 60
        assert len(s.findings) == 0
        assert s.summary is None

    def test_within_budget(self, sample_agent_session):
        s = sample_agent_session
        s.tool_call_count = 59
        assert s.tool_call_count < s.max_tool_calls


class TestPRSummary:
    def test_defaults(self):
        s = PRSummary(
            overview="Good PR", intent="Fix bug",
            risk_level=Severity.LOW,
        )
        assert s.findings_by_severity == {}
        assert s.key_concerns == []
        assert s.test_stubs == []


class TestDependencyNode:
    def test_defaults(self):
        node = DependencyNode(path="foo.py")
        assert node.imports == []
        assert node.imported_by == []
        assert node.defined_symbols == []


class TestSymbolInfo:
    def test_call_sites(self):
        cs = CallSite(file="a.py", line=10, snippet="foo()")
        info = SymbolInfo(name="foo", kind="function", defined_in="b.py", defined_at_line=5, call_sites=[cs])
        assert len(info.call_sites) == 1
        assert info.call_sites[0].file == "a.py"
