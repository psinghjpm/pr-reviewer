"""Shared pytest fixtures for all test suites."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

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
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# Sample data factories
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_pr_metadata() -> PRMetadata:
    return PRMetadata(
        pr_id=42,
        title="Add payment processing module",
        description="Implements Stripe payment integration.",
        author="alice",
        source_branch="feature/payment",
        target_branch="main",
        base_sha="abc1234",
        head_sha="def5678",
        platform=Platform.GITHUB,
        repo_full_name="acme/myrepo",
        url="https://github.com/acme/myrepo/pull/42",
        is_draft=False,
    )


@pytest.fixture
def sample_hunk_line_added() -> HunkLine:
    return HunkLine(line_type="+", value="    return result\n", target_line_no=15)


@pytest.fixture
def sample_hunk() -> Hunk:
    return Hunk(
        source_start=10,
        source_length=5,
        target_start=10,
        target_length=6,
        section_header="def process()",
        lines=[
            HunkLine(line_type=" ", value="    data = fetch()\n", source_line_no=10, target_line_no=10),
            HunkLine(line_type="-", value="    return None\n", source_line_no=11),
            HunkLine(line_type="+", value="    result = transform(data)\n", target_line_no=11),
            HunkLine(line_type="+", value="    return result\n", target_line_no=12),
        ],
    )


@pytest.fixture
def sample_file_diff(sample_hunk: Hunk) -> FileDiff:
    return FileDiff(
        path="src/payment.py",
        status=DiffStatus.MODIFIED,
        hunks=[sample_hunk],
        additions=2,
        deletions=1,
    )


@pytest.fixture
def sample_finding() -> ReviewFinding:
    return ReviewFinding(
        file="src/payment.py",
        line_start=42,
        line_end=42,
        severity=Severity.HIGH,
        category=ReviewCategory.SECURITY,
        message="SQL query constructed with string formatting — potential injection vulnerability.",
        suggestion="Use parameterized queries: cursor.execute('SELECT * FROM users WHERE id = %s', (user_id,))",
        confidence=0.92,
    )


@pytest.fixture
def sample_agent_session(sample_pr_metadata: PRMetadata, sample_file_diff: FileDiff) -> AgentSession:
    return AgentSession(
        pr_metadata=sample_pr_metadata,
        diff=[sample_file_diff],
    )


# ---------------------------------------------------------------------------
# Mock adapter fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_adapter(sample_pr_metadata: PRMetadata, sample_file_diff: FileDiff) -> MagicMock:
    adapter = MagicMock()
    adapter.get_pr_metadata.return_value = sample_pr_metadata
    adapter.get_pr_diff.return_value = [sample_file_diff]
    adapter.get_file_content.return_value = "# sample file\ndef foo():\n    pass\n"
    adapter.list_repo_files.return_value = ["src/payment.py", "tests/test_payment.py"]
    adapter.search_repo_code.return_value = []
    adapter.get_existing_comments.return_value = []
    adapter.get_recent_commits.return_value = [
        {"sha": "abc1234", "message": "Initial commit", "author": "alice", "date": "2024-01-01"}
    ]
    adapter.post_inline_comment.return_value = None
    adapter.post_pr_summary.return_value = None
    return adapter


# ---------------------------------------------------------------------------
# Sample diff text (for diff parser tests)
# ---------------------------------------------------------------------------

SAMPLE_DIFF = """\
diff --git a/src/payment.py b/src/payment.py
index 1234567..abcdefg 100644
--- a/src/payment.py
+++ b/src/payment.py
@@ -10,2 +10,3 @@ def process():
     data = fetch()
-    return None
+    result = transform(data)
+    return result

diff --git a/src/new_module.py b/src/new_module.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/src/new_module.py
@@ -0,0 +1,5 @@
+# New module.
+
+
+def hello():
+    return "hello"
"""


@pytest.fixture
def sample_diff_text() -> str:
    return SAMPLE_DIFF
