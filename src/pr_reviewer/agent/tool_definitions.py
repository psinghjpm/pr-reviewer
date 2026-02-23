"""Tool schemas for the Claude API tool_use feature."""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Tool schemas (Anthropic API format)
# ---------------------------------------------------------------------------

AGENT_TOOLS: list[dict[str, Any]] = [
    {
        "name": "fetch_full_file",
        "description": (
            "Fetch the complete content of a file from the repository. "
            "Use this to read files beyond the diff hunks — e.g. to understand "
            "full class context, imports, or helper functions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-relative file path, e.g. 'src/foo/bar.py'",
                },
                "ref": {
                    "type": "string",
                    "description": "Git ref (SHA or branch). Defaults to the PR head SHA.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "search_symbol",
        "description": (
            "Find where a function, class, or method is defined in the repo "
            "and list all call sites (Greptile-style multi-hop tracing). "
            "Returns definition location and up to 50 callers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Exact symbol name, e.g. 'process_payment'",
                },
                "kind": {
                    "type": "string",
                    "enum": ["function", "class", "method"],
                    "description": "Symbol kind. Default: 'function'.",
                },
            },
            "required": ["name"],
        },
    },
    {
        "name": "get_file_dependencies",
        "description": (
            "Return the import dependency graph for a file: what it imports "
            "and what imports it (blast-radius). Uses tree-sitter AST parsing."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-relative file path.",
                },
                "depth": {
                    "type": "integer",
                    "description": "Hop depth for BFS traversal. Default: 2.",
                    "minimum": 1,
                    "maximum": 5,
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_related_tests",
        "description": (
            "Find test files that cover the given source file. "
            "Tries 5 naming conventions plus import-based fallback. "
            "Returns test file paths and their content."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "source_path": {
                    "type": "string",
                    "description": "Repo-relative path to the source file, e.g. 'src/pr_reviewer/config.py'",
                },
            },
            "required": ["source_path"],
        },
    },
    {
        "name": "search_codebase",
        "description": (
            "Search the entire codebase for a text pattern (regex or literal). "
            "Returns matching files, line numbers, and snippets. Capped at 50 results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Search query or regex pattern.",
                },
                "file_pattern": {
                    "type": "string",
                    "description": "Glob file filter, e.g. '*.py'. Default: '*.py'.",
                },
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "list_directory",
        "description": "List files and directories at a given repo path.",
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-relative directory path. Use '.' for root.",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_git_history",
        "description": (
            "Get the recent commit history for a file to understand intent, "
            "authorship, and recent change patterns."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Repo-relative file path.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max number of commits to return. Default: 10.",
                    "minimum": 1,
                    "maximum": 50,
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "get_pr_history_comments",
        "description": (
            "Fetch existing comments on this PR. Used to avoid posting duplicate "
            "review comments and to understand prior review context."
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "emit_finding",
        "description": (
            "Record a code review finding. Call this for every issue discovered. "
            "Findings are deduplicated and filtered before posting."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file": {
                    "type": "string",
                    "description": "Repo-relative file path where the issue is located.",
                },
                "line_start": {
                    "type": "integer",
                    "description": "First line of the problematic code (1-based).",
                },
                "line_end": {
                    "type": "integer",
                    "description": "Last line of the problematic code (1-based).",
                },
                "severity": {
                    "type": "string",
                    "enum": ["CRITICAL", "HIGH", "MEDIUM", "LOW", "INFO"],
                    "description": "Issue severity.",
                },
                "category": {
                    "type": "string",
                    "enum": [
                        "BUG",
                        "LOGIC",
                        "SECURITY",
                        "PERFORMANCE",
                        "MAINTAINABILITY",
                        "TEST_COVERAGE",
                        "MISSING_TEST",
                        "STYLE",
                    ],
                    "description": "Issue category.",
                },
                "message": {
                    "type": "string",
                    "description": "Clear, concise description of the issue.",
                },
                "suggestion": {
                    "type": "string",
                    "description": (
                        "Concrete fix or improvement suggestion. "
                        "Include corrected code snippet when possible."
                    ),
                },
                "confidence": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1.0,
                    "description": "Confidence score (0.0–1.0). Omit findings below 0.5.",
                },
            },
            "required": ["file", "line_start", "line_end", "severity", "category", "message"],
        },
    },
]
