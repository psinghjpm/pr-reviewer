"""Dependency tracer: tree-sitter AST → networkx import graph.

Parses Python files via tree-sitter to extract import statements and build
a directed dependency graph. Used to compute blast-radius and import chains.
"""

from __future__ import annotations

import re
from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

import networkx as nx
import structlog

from pr_reviewer.models import DependencyNode

if TYPE_CHECKING:
    from pr_reviewer.platforms.base import PlatformAdapter

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# tree-sitter setup (lazy import to avoid hard failure if package missing)
# ---------------------------------------------------------------------------

_PARSER = None
_PY_LANGUAGE = None


def _get_parser():  # type: ignore[return]
    global _PARSER, _PY_LANGUAGE
    if _PARSER is not None:
        return _PARSER, _PY_LANGUAGE
    try:
        from tree_sitter import Parser
        from tree_sitter_language_pack import get_language
        _PY_LANGUAGE = get_language("python")
        _PARSER = Parser(_PY_LANGUAGE)
        return _PARSER, _PY_LANGUAGE
    except Exception as exc:
        logger.warning("tree_sitter_unavailable", error=str(exc), fallback="regex")
        return None, None


# ---------------------------------------------------------------------------
# Regex fallback for import extraction
# ---------------------------------------------------------------------------

_IMPORT_RE = re.compile(
    r"^(?:from\s+([\w.]+)\s+import|import\s+([\w.,\s]+))",
    re.MULTILINE,
)


def _extract_imports_regex(source: str) -> list[str]:
    """Fast regex-based import extractor (fallback when tree-sitter unavailable)."""
    modules: list[str] = []
    for m in _IMPORT_RE.finditer(source):
        if m.group(1):
            modules.append(m.group(1))
        elif m.group(2):
            for part in m.group(2).split(","):
                modules.append(part.strip().split(" ")[0])
    return [mod for mod in modules if mod]


def _extract_imports_treesitter(source: str) -> list[str]:
    """tree-sitter-based import extractor."""
    parser, language = _get_parser()
    if parser is None:
        return _extract_imports_regex(source)

    tree = parser.parse(source.encode())
    modules: list[str] = []

    # Walk tree manually for import nodes
    def walk(node):  # type: ignore[no-untyped-def]
        if node.type in ("import_statement", "import_from_statement"):
            text = source[node.start_byte:node.end_byte]
            modules.extend(_extract_imports_regex(text))
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return modules


def _extract_defined_symbols_treesitter(source: str) -> list[str]:
    """Extract function and class names defined in source."""
    parser, language = _get_parser()
    symbols: list[str] = []

    if parser is None:
        # Regex fallback
        for m in re.finditer(r"^(?:def|class)\s+(\w+)", source, re.MULTILINE):
            symbols.append(m.group(1))
        return symbols

    tree = parser.parse(source.encode())

    def walk(node):  # type: ignore[no-untyped-def]
        if node.type in ("function_definition", "class_definition"):
            for child in node.children:
                if child.type == "identifier":
                    symbols.append(child.text.decode())
                    break
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return symbols


def _module_to_path(module: str, repo_files: list[str]) -> str | None:
    """Convert a dotted module name to a file path that exists in the repo."""
    # Try direct mapping: foo.bar → foo/bar.py
    candidate = module.replace(".", "/") + ".py"
    for f in repo_files:
        if f == candidate or f.endswith("/" + candidate):
            return f
    # Try __init__.py
    pkg_candidate = module.replace(".", "/") + "/__init__.py"
    for f in repo_files:
        if f == pkg_candidate or f.endswith("/" + pkg_candidate):
            return f
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class DependencyTracer:
    """Build and query an import dependency graph for a repository.

    The graph is built lazily: only files reachable within *max_depth* hops
    from the seed set (changed files) are parsed.
    """

    def __init__(
        self,
        adapter: "PlatformAdapter",
        ref: str,
        repo_files: list[str] | None = None,
        max_depth: int = 3,
    ) -> None:
        self._adapter = adapter
        self._ref = ref
        self._repo_files: list[str] = repo_files or []
        self._max_depth = max_depth
        self._graph: nx.DiGraph = nx.DiGraph()
        self._parsed: set[str] = set()
        self._symbol_cache: dict[str, list[str]] = {}

    # ------------------------------------------------------------------

    def build_graph(self, seed_paths: list[str]) -> None:
        """BFS from *seed_paths*, parsing up to *max_depth* hops."""
        queue: deque[tuple[str, int]] = deque((p, 0) for p in seed_paths)
        while queue:
            path, depth = queue.popleft()
            if path in self._parsed or depth > self._max_depth:
                continue
            self._parse_file(path)
            if depth < self._max_depth:
                for neighbour in list(self._graph.successors(path)):
                    queue.append((neighbour, depth + 1))
                for neighbour in list(self._graph.predecessors(path)):
                    queue.append((neighbour, depth + 1))

    def _parse_file(self, path: str) -> None:
        if path in self._parsed:
            return
        self._parsed.add(path)
        self._graph.add_node(path)

        content = self._adapter.get_file_content(path, self._ref)
        if not content:
            return

        modules = _extract_imports_treesitter(content)
        symbols = _extract_defined_symbols_treesitter(content)
        self._symbol_cache[path] = symbols

        for mod in modules:
            dep_path = _module_to_path(mod, self._repo_files)
            if dep_path and dep_path != path:
                self._graph.add_edge(path, dep_path)

    # ------------------------------------------------------------------

    def get_dependencies(self, path: str, depth: int = 2) -> DependencyNode:
        """Return a DependencyNode describing imports and importers of *path*."""
        if path not in self._parsed:
            self.build_graph([path])

        imports: list[str] = []
        imported_by: list[str] = []

        try:
            # BFS successors (what this file imports)
            for node in nx.bfs_tree(self._graph, path, depth_limit=depth).nodes():
                if node != path:
                    imports.append(node)

            # BFS predecessors (what imports this file — blast radius)
            rev = self._graph.reverse()
            for node in nx.bfs_tree(rev, path, depth_limit=depth).nodes():
                if node != path:
                    imported_by.append(node)
        except nx.NetworkXError:
            pass

        return DependencyNode(
            path=path,
            imports=imports,
            imported_by=imported_by,
            defined_symbols=self._symbol_cache.get(path, []),
        )

    def get_graph(self) -> nx.DiGraph:
        return self._graph
