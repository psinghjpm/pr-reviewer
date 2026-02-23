"""Map source files to their corresponding test files using 5 naming conventions."""

from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from pr_reviewer.context.dependency_tracer import DependencyTracer
    from pr_reviewer.platforms.base import PlatformAdapter

logger = structlog.get_logger(__name__)


class RelatedTestFinder:
    """Find test files related to a given source file.

    Conventions tried in order:
    1. tests/test_{stem}.py
    2. tests/unit/test_{stem}.py  OR  tests/integration/test_{stem}.py
    3. {same_dir}/test_{stem}.py
    4. Mirror src structure: src/foo/bar.py → tests/foo/test_bar.py
    5. Fallback: any file in repo_files that imports the source module
    """

    def __init__(
        self,
        adapter: "PlatformAdapter",
        ref: str,
        repo_files: list[str],
        tracer: "DependencyTracer | None" = None,
    ) -> None:
        self._adapter = adapter
        self._ref = ref
        self._repo_files = repo_files
        self._tracer = tracer
        self._repo_file_set = set(repo_files)

    def find_test_files(self, source_path: str) -> list[str]:
        """Return a list of test file paths related to *source_path*."""
        stem = Path(source_path).stem
        # Use posix-style paths throughout for cross-platform consistency
        parent = Path(source_path).parent.as_posix()
        found: list[str] = []

        # Convention 1: tests/test_{stem}.py
        c1 = f"tests/test_{stem}.py"
        if c1 in self._repo_file_set:
            found.append(c1)

        # Convention 2: tests/unit/ and tests/integration/
        for subdir in ("unit", "integration"):
            c2 = f"tests/{subdir}/test_{stem}.py"
            if c2 in self._repo_file_set:
                found.append(c2)

        # Convention 3: {same_dir}/test_{stem}.py
        c3 = f"{parent}/test_{stem}.py" if parent and parent != "." else f"test_{stem}.py"
        if c3 in self._repo_file_set:
            found.append(c3)

        # Convention 4: mirror src→tests structure
        c4 = self._mirror_path(source_path, stem)
        if c4 and c4 in self._repo_file_set and c4 not in found:
            found.append(c4)

        # Convention 5: fallback via dependency tracer (files that import this module)
        if not found and self._tracer is not None:
            found.extend(self._find_via_imports(source_path))

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for f in found:
            if f not in seen:
                seen.add(f)
                unique.append(f)

        return unique

    def fetch_test_content(self, source_path: str) -> dict[str, str]:
        """Return {test_file_path: content} for all related test files."""
        paths = self.find_test_files(source_path)
        result: dict[str, str] = {}
        for path in paths:
            content = self._adapter.get_file_content(path, self._ref)
            if content:
                result[path] = content
        return result

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _mirror_path(source_path: str, stem: str) -> str | None:
        """Convert src/foo/bar.py → tests/foo/test_bar.py."""
        parts = Path(source_path).parts
        if "src" in parts:
            idx = list(parts).index("src")
            rest = parts[idx + 1:]  # e.g. ('pr_reviewer', 'config.py')
            if len(rest) >= 1:
                dir_parts = rest[:-1]  # everything except the file
                test_file = f"test_{stem}.py"
                # Use posix-style paths (forward slashes) for cross-platform consistency
                return Path("tests").joinpath(*dir_parts, test_file).as_posix()
        return None

    def _find_via_imports(self, source_path: str) -> list[str]:
        """Use DependencyTracer to find files that import *source_path*."""
        if self._tracer is None:
            return []
        self._tracer.build_graph([source_path])
        node = self._tracer.get_dependencies(source_path, depth=1)
        test_files = [
            p for p in node.imported_by
            if "test" in Path(p).stem.lower() and p.endswith(".py")
        ]
        return test_files
