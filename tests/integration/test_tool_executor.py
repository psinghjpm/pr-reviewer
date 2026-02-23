"""Integration tests for ToolExecutor using mock adapter (no live network)."""

from unittest.mock import MagicMock

import pytest

from pr_reviewer.agent.tool_executor import ToolExecutor
from pr_reviewer.models import (
    AgentSession,
    DiffStatus,
    FileDiff,
    Platform,
    PRMetadata,
    ReviewCategory,
    Severity,
)


@pytest.fixture
def session(sample_pr_metadata, sample_file_diff):
    return AgentSession(
        pr_metadata=sample_pr_metadata,
        diff=[sample_file_diff],
    )


@pytest.fixture
def executor(mock_adapter, session):
    return ToolExecutor(adapter=mock_adapter, session=session)


class TestToolExecutorFetchFile:
    def test_returns_file_content(self, executor, mock_adapter):
        mock_adapter.get_file_content.return_value = "def foo(): pass"
        result = executor.execute("fetch_full_file", {"path": "src/foo.py"}, "id1")
        assert "foo" in result.content
        assert not result.is_error

    def test_missing_file(self, executor, mock_adapter):
        mock_adapter.get_file_content.return_value = ""
        result = executor.execute("fetch_full_file", {"path": "missing.py"}, "id2")
        assert "not found" in result.content.lower() or "empty" in result.content.lower()

    def test_truncation(self, executor, mock_adapter):
        mock_adapter.get_file_content.return_value = "x" * 20000
        result = executor.execute("fetch_full_file", {"path": "big.py"}, "id3")
        assert "truncated" in result.content.lower()
        assert len(result.content) <= executor._max_len + 200


class TestToolExecutorSearchSymbol:
    def test_symbol_not_found(self, executor, mock_adapter):
        mock_adapter.list_repo_files.return_value = []
        result = executor.execute("search_symbol", {"name": "nonexistent_fn"}, "id4")
        assert "not found" in result.content.lower()


class TestToolExecutorGitHistory:
    def test_returns_history(self, executor, mock_adapter):
        mock_adapter.get_recent_commits.return_value = [
            {"sha": "abc123", "message": "Fix bug", "author": "alice", "date": "2024-01-01"}
        ]
        result = executor.execute("get_git_history", {"path": "src/foo.py", "limit": 5}, "id5")
        assert "Fix bug" in result.content
        assert "alice" in result.content

    def test_no_history(self, executor, mock_adapter):
        mock_adapter.get_recent_commits.return_value = []
        result = executor.execute("get_git_history", {"path": "src/foo.py"}, "id6")
        assert "No recent commits" in result.content


class TestToolExecutorSearchCodebase:
    def test_with_results(self, executor, mock_adapter):
        mock_adapter.search_repo_code.return_value = [
            {"path": "src/foo.py", "line": 5, "snippet": "def foo():"}
        ]
        result = executor.execute("search_codebase", {"pattern": "foo"}, "id7")
        assert "src/foo.py" in result.content
        assert "def foo" in result.content

    def test_no_results(self, executor, mock_adapter):
        mock_adapter.search_repo_code.return_value = []
        result = executor.execute("search_codebase", {"pattern": "zzz_nonexistent"}, "id8")
        assert "No results" in result.content


class TestToolExecutorEmitFinding:
    def test_emit_finding(self, executor, session):
        finding = executor.handle_emit_finding({
            "file": "src/foo.py",
            "line_start": 10,
            "line_end": 10,
            "severity": "HIGH",
            "category": "BUG",
            "message": "Null pointer dereference",
            "suggestion": "Check for None before access",
            "confidence": 0.9,
        })
        assert finding.severity == Severity.HIGH
        assert finding.category == ReviewCategory.BUG
        assert finding in session.findings

    def test_emit_finding_defaults(self, executor, session):
        finding = executor.handle_emit_finding({
            "file": "src/bar.py",
            "line_start": 5,
            "line_end": 5,
            "severity": "LOW",
            "category": "STYLE",
            "message": "Style issue",
        })
        assert finding.confidence == 0.8
        assert finding.suggestion == ""


class TestToolExecutorUnknownTool:
    def test_unknown_tool(self, executor):
        result = executor.execute("nonexistent_tool", {}, "id9")
        assert result.is_error
        assert "Unknown tool" in result.content


class TestToolExecutorGetPRComments:
    def test_no_existing_comments(self, executor, mock_adapter):
        mock_adapter.get_existing_comments.return_value = []
        result = executor.execute("get_pr_history_comments", {}, "id10")
        assert "No existing comments" in result.content

    def test_with_existing_comments(self, executor, mock_adapter):
        mock_adapter.get_existing_comments.return_value = [
            {"path": "src/foo.py", "line": 10, "body": "Please fix this.", "id": 1}
        ]
        result = executor.execute("get_pr_history_comments", {}, "id11")
        assert "Please fix this" in result.content
