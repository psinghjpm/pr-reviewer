"""Unit tests for the diff parser."""

import pytest

from pr_reviewer.models import DiffStatus
from pr_reviewer.utils.diff_parser import diff_summary, parse_diff


SIMPLE_DIFF = """\
diff --git a/src/foo.py b/src/foo.py
index 1234567..abcdefg 100644
--- a/src/foo.py
+++ b/src/foo.py
@@ -1,2 +1,3 @@
 def foo():
-    pass
+    x = 1
+    return x
"""

NEW_FILE_DIFF = """\
diff --git a/src/new.py b/src/new.py
new file mode 100644
index 0000000..1111111
--- /dev/null
+++ b/src/new.py
@@ -0,0 +1,3 @@
+def bar():
+    return 42
+
"""

DELETED_FILE_DIFF = """\
diff --git a/old.py b/old.py
deleted file mode 100644
index abcdefg..0000000
--- a/old.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def old():
-    pass
-
"""


class TestParseDiff:
    def test_empty_string(self):
        result = parse_diff("")
        assert result == []

    def test_whitespace_only(self):
        result = parse_diff("   \n  ")
        assert result == []

    def test_simple_modification(self):
        result = parse_diff(SIMPLE_DIFF)
        assert len(result) == 1
        fd = result[0]
        assert fd.path == "src/foo.py"
        assert fd.status == DiffStatus.MODIFIED
        assert fd.additions == 2
        assert fd.deletions == 1

    def test_new_file(self):
        result = parse_diff(NEW_FILE_DIFF)
        assert len(result) == 1
        fd = result[0]
        assert fd.status == DiffStatus.ADDED
        assert fd.additions > 0
        assert fd.deletions == 0

    def test_deleted_file(self):
        result = parse_diff(DELETED_FILE_DIFF)
        assert len(result) == 1
        fd = result[0]
        assert fd.status == DiffStatus.DELETED
        assert fd.deletions > 0

    def test_hunk_lines(self):
        result = parse_diff(SIMPLE_DIFF)
        fd = result[0]
        assert len(fd.hunks) == 1
        hunk = fd.hunks[0]
        line_types = [l.line_type for l in hunk.lines]
        assert "+" in line_types
        assert "-" in line_types

    def test_changed_line_numbers(self):
        result = parse_diff(SIMPLE_DIFF)
        fd = result[0]
        changed = fd.changed_line_numbers
        assert all(isinstance(n, int) for n in changed)
        assert len(changed) > 0

    def test_multiple_files(self, sample_diff_text):
        result = parse_diff(sample_diff_text)
        assert len(result) >= 2


class TestDiffSummary:
    def test_empty(self):
        summary = diff_summary([])
        assert "No changes" in summary

    def test_with_files(self, sample_file_diff):
        summary = diff_summary([sample_file_diff])
        assert "1 file" in summary
        assert "payment.py" in summary
