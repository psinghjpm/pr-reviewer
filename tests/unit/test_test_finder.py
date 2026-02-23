"""Unit tests for RelatedTestFinder."""

from unittest.mock import MagicMock

import pytest

from pr_reviewer.context.test_finder import RelatedTestFinder


@pytest.fixture
def repo_files():
    return [
        "src/pr_reviewer/config.py",
        "src/pr_reviewer/models.py",
        "tests/test_config.py",
        "tests/unit/test_models.py",
        "tests/integration/test_config.py",
        "src/pr_reviewer/test_config.py",  # convention 3 same_dir equivalent
        "tests/pr_reviewer/test_config.py",  # convention 4 mirror
    ]


@pytest.fixture
def finder(repo_files):
    adapter = MagicMock()
    adapter.get_file_content.return_value = "# test content"
    return RelatedTestFinder(adapter=adapter, ref="main", repo_files=repo_files)


class TestRelatedTestFinder:
    def test_convention_1(self, finder):
        result = finder.find_test_files("src/pr_reviewer/config.py")
        assert "tests/test_config.py" in result

    def test_convention_2_unit(self, finder):
        result = finder.find_test_files("src/pr_reviewer/models.py")
        assert "tests/unit/test_models.py" in result

    def test_convention_2_integration(self, finder):
        result = finder.find_test_files("src/pr_reviewer/config.py")
        assert "tests/integration/test_config.py" in result

    def test_convention_4_mirror(self, finder):
        result = finder.find_test_files("src/pr_reviewer/config.py")
        assert "tests/pr_reviewer/test_config.py" in result

    def test_no_duplicates(self, finder):
        result = finder.find_test_files("src/pr_reviewer/config.py")
        assert len(result) == len(set(result))

    def test_nonexistent_source(self, finder):
        result = finder.find_test_files("src/nonexistent.py")
        assert result == []

    def test_fetch_test_content(self, finder):
        content = finder.fetch_test_content("src/pr_reviewer/config.py")
        # Should have at least one entry
        assert len(content) >= 1
        for path, text in content.items():
            assert isinstance(path, str)
            assert isinstance(text, str)

    def test_mirror_path_calculation(self):
        # Use posix paths for comparison (as_posix() always uses forward slashes)
        result = RelatedTestFinder._mirror_path("src/foo/bar.py", "bar")
        assert result is not None
        assert result.replace("\\", "/") == "tests/foo/test_bar.py"

        result2 = RelatedTestFinder._mirror_path("src/bar.py", "bar")
        assert result2 is not None
        assert result2.replace("\\", "/") == "tests/test_bar.py"

        assert RelatedTestFinder._mirror_path("no_src/bar.py", "bar") is None
