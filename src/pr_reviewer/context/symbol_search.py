"""Symbol search: find function/class definitions and call sites via tree-sitter."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import structlog

from pr_reviewer.models import CallSite, SymbolInfo

if TYPE_CHECKING:
    from pr_reviewer.platforms.base import PlatformAdapter

logger = structlog.get_logger(__name__)

_MAX_FILES = 500  # cap to avoid scanning huge repos


def _get_parser():  # type: ignore[return]
    try:
        from tree_sitter import Parser
        from tree_sitter_language_pack import get_language
        lang = get_language("python")
        parser = Parser(lang)
        return parser, lang
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Regex fallbacks
# ---------------------------------------------------------------------------

def _find_definition_regex(source: str, name: str, kind: str) -> int | None:
    """Return 1-based line number of the definition, or None."""
    if kind in ("function", "method"):
        pattern = re.compile(rf"^\s*(?:async\s+)?def\s+{re.escape(name)}\s*\(", re.MULTILINE)
    else:
        pattern = re.compile(rf"^\s*class\s+{re.escape(name)}\s*[:(]", re.MULTILINE)

    for i, line in enumerate(source.splitlines(), 1):
        if pattern.search(line):
            return i
    return None


def _find_call_sites_regex(source: str, name: str, path: str) -> list[CallSite]:
    pattern = re.compile(rf"\b{re.escape(name)}\s*\(")
    sites: list[CallSite] = []
    for i, line in enumerate(source.splitlines(), 1):
        if pattern.search(line):
            sites.append(CallSite(file=path, line=i, snippet=line.strip()[:120]))
    return sites


# ---------------------------------------------------------------------------
# tree-sitter variants
# ---------------------------------------------------------------------------

def _find_definition_ts(source: str, name: str, kind: str, parser, language) -> int | None:  # type: ignore[no-untyped-def]
    tree = parser.parse(source.encode())
    lines = source.splitlines()

    def walk(node):  # type: ignore[no-untyped-def]
        if kind in ("function", "method") and node.type == "function_definition":
            for child in node.children:
                if child.type == "identifier" and child.text.decode() == name:
                    return node.start_point[0] + 1  # 1-based
        elif kind == "class" and node.type == "class_definition":
            for child in node.children:
                if child.type == "identifier" and child.text.decode() == name:
                    return node.start_point[0] + 1
        for child in node.children:
            result = walk(child)
            if result is not None:
                return result
        return None

    return walk(tree.root_node)


def _find_call_sites_ts(source: str, name: str, path: str, parser, language) -> list[CallSite]:  # type: ignore[no-untyped-def]
    tree = parser.parse(source.encode())
    sites: list[CallSite] = []
    lines = source.splitlines()

    def walk(node):  # type: ignore[no-untyped-def]
        if node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node is not None:
                func_text = source[func_node.start_byte:func_node.end_byte]
                # Match exact name or attribute access ending in name
                if func_text == name or func_text.endswith(f".{name}"):
                    line_no = node.start_point[0] + 1
                    snippet = lines[node.start_point[0]][:120] if node.start_point[0] < len(lines) else ""
                    sites.append(CallSite(file=path, line=line_no, snippet=snippet.strip()))
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return sites


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class SymbolSearch:
    """Search for symbol definitions and call sites across a repository."""

    def __init__(self, adapter: "PlatformAdapter", ref: str, repo_files: list[str]) -> None:
        self._adapter = adapter
        self._ref = ref
        self._repo_files = repo_files
        self._parser, self._language = _get_parser()

    def find_symbol(self, name: str, kind: str = "function") -> SymbolInfo | None:
        """Find the definition of *name* (function/class/method) and its call sites.

        Skips files that don't contain the symbol name (fast substring pre-filter).
        Returns None if the symbol cannot be found.
        """
        definition_file: str | None = None
        definition_line: int = 0
        call_sites: list[CallSite] = []

        py_files = [f for f in self._repo_files if f.endswith(".py")][:_MAX_FILES]

        for path in py_files:
            content = self._adapter.get_file_content(path, self._ref)
            if not content or name not in content:
                continue  # fast pre-filter

            # Look for definition
            if definition_file is None:
                if self._parser:
                    line = _find_definition_ts(content, name, kind, self._parser, self._language)
                else:
                    line = _find_definition_regex(content, name, kind)
                if line is not None:
                    definition_file = path
                    definition_line = line

            # Look for call sites
            if self._parser:
                sites = _find_call_sites_ts(content, name, path, self._parser, self._language)
            else:
                sites = _find_call_sites_regex(content, name, path)

            call_sites.extend(sites)

        if definition_file is None:
            logger.debug("symbol_not_found", name=name, kind=kind)
            return None

        # Deduplicate call sites (same file+line)
        seen: set[tuple[str, int]] = set()
        unique_sites: list[CallSite] = []
        for cs in call_sites:
            key = (cs.file, cs.line)
            if key not in seen:
                seen.add(key)
                unique_sites.append(cs)

        return SymbolInfo(
            name=name,
            kind=kind,
            defined_in=definition_file,
            defined_at_line=definition_line,
            call_sites=unique_sites,
        )
