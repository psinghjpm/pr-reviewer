"""All Pydantic data models for pr-reviewer."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class Platform(str, Enum):
    GITHUB = "github"
    BITBUCKET = "bitbucket"


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    INFO = "INFO"

    def __lt__(self, other: "Severity") -> bool:
        order = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        return order.index(self) < order.index(other)

    def __le__(self, other: "Severity") -> bool:
        return self == other or self < other

    def __gt__(self, other: "Severity") -> bool:
        return not self <= other

    def __ge__(self, other: "Severity") -> bool:
        return not self < other


class ReviewCategory(str, Enum):
    BUG = "BUG"
    LOGIC = "LOGIC"
    SECURITY = "SECURITY"
    PERFORMANCE = "PERFORMANCE"
    MAINTAINABILITY = "MAINTAINABILITY"
    TEST_COVERAGE = "TEST_COVERAGE"
    MISSING_TEST = "MISSING_TEST"
    STYLE = "STYLE"


class DiffStatus(str, Enum):
    ADDED = "added"
    MODIFIED = "modified"
    DELETED = "deleted"
    RENAMED = "renamed"


# ---------------------------------------------------------------------------
# Diff Models
# ---------------------------------------------------------------------------


class HunkLine(BaseModel):
    """A single line within a hunk."""

    line_type: str  # "+" | "-" | " "
    value: str
    source_line_no: int | None = None
    target_line_no: int | None = None


class Hunk(BaseModel):
    """A single diff hunk (contiguous block of changes)."""

    source_start: int
    source_length: int
    target_start: int
    target_length: int
    section_header: str = ""
    lines: list[HunkLine] = Field(default_factory=list)


class FileDiff(BaseModel):
    """Parsed diff for a single file."""

    path: str                          # new path (or old path for deletions)
    old_path: str | None = None        # set for renames
    status: DiffStatus
    hunks: list[Hunk] = Field(default_factory=list)
    additions: int = 0
    deletions: int = 0

    @property
    def changed_line_numbers(self) -> list[int]:
        """Return list of target (new file) line numbers that were added or changed."""
        lines = []
        for hunk in self.hunks:
            for line in hunk.lines:
                if line.line_type == "+" and line.target_line_no is not None:
                    lines.append(line.target_line_no)
        return lines


# ---------------------------------------------------------------------------
# PR Metadata
# ---------------------------------------------------------------------------


class PRMetadata(BaseModel):
    """Core metadata about a pull request."""

    pr_id: int | str
    title: str
    description: str = ""
    author: str
    source_branch: str
    target_branch: str
    base_sha: str
    head_sha: str
    platform: Platform
    repo_full_name: str   # "owner/repo"
    url: str
    is_draft: bool = False


# ---------------------------------------------------------------------------
# Review Findings
# ---------------------------------------------------------------------------


class ReviewFinding(BaseModel):
    """A single code review finding emitted by the agent."""

    file: str
    line_start: int
    line_end: int
    severity: Severity
    category: ReviewCategory
    message: str
    suggestion: str = ""
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)

    @property
    def fingerprint(self) -> str:
        """Unique fingerprint for deduplication."""
        body_prefix = self.message[:80]
        return f"{self.file}:{self.line_start}:{body_prefix}"


# ---------------------------------------------------------------------------
# Test Stubs
# ---------------------------------------------------------------------------


class TestStub(BaseModel):
    """A pytest test stub generated for a new function/class."""

    function_name: str
    test_name: str
    source_file: str
    stub_code: str
    description: str = ""


# ---------------------------------------------------------------------------
# PR Summary
# ---------------------------------------------------------------------------


class PRSummary(BaseModel):
    """High-level summary of the entire PR review."""

    overview: str
    intent: str
    risk_level: Severity
    findings_by_severity: dict[str, int] = Field(default_factory=dict)
    key_concerns: list[str] = Field(default_factory=list)
    test_stubs: list[TestStub] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Context / Dependency Models
# ---------------------------------------------------------------------------


class CallSite(BaseModel):
    """A location where a symbol is called."""

    file: str
    line: int
    snippet: str = ""


class SymbolInfo(BaseModel):
    """Information about a function or class definition and its callers."""

    name: str
    kind: str  # "function" | "class" | "method"
    defined_in: str
    defined_at_line: int
    call_sites: list[CallSite] = Field(default_factory=list)
    docstring: str = ""


class DependencyNode(BaseModel):
    """Dependency graph node for a single file."""

    path: str
    imports: list[str] = Field(default_factory=list)          # files this file imports
    imported_by: list[str] = Field(default_factory=list)      # files that import this file
    defined_symbols: list[str] = Field(default_factory=list)  # functions/classes defined here
    called_symbols: list[str] = Field(default_factory=list)   # symbols called here


# ---------------------------------------------------------------------------
# Agent Session
# ---------------------------------------------------------------------------


class AgentSession(BaseModel):
    """Mutable state threaded through the agent review loop."""

    model_config = {"arbitrary_types_allowed": True}

    pr_metadata: PRMetadata
    diff: list[FileDiff]
    context_fetched: dict[str, str] = Field(default_factory=dict)
    tool_call_count: int = 0
    max_tool_calls: int = 60
    findings: list[ReviewFinding] = Field(default_factory=list)
    summary: PRSummary | None = None


# ---------------------------------------------------------------------------
# Config Model
# ---------------------------------------------------------------------------


class AnthropicConfig(BaseModel):
    api_key: str = ""
    model: str = "claude-sonnet-4-6"
    max_tool_calls: int = 60


class GitHubConfig(BaseModel):
    token: str = ""


class BitbucketConfig(BaseModel):
    username: str = ""
    app_password: str = ""


class ReviewConfig(BaseModel):
    min_severity_to_post: Severity = Severity.LOW
    max_inline_comments: int = 30
    max_content_length: int = 12000
    cache_ttl_seconds: int = 300


class CacheConfig(BaseModel):
    directory: str = ".pr_reviewer_cache"
    ttl_seconds: int = 300


class AppConfig(BaseModel):
    anthropic: AnthropicConfig = Field(default_factory=AnthropicConfig)
    github: GitHubConfig = Field(default_factory=GitHubConfig)
    bitbucket: BitbucketConfig = Field(default_factory=BitbucketConfig)
    review: ReviewConfig = Field(default_factory=ReviewConfig)
    cache: CacheConfig = Field(default_factory=CacheConfig)


# ---------------------------------------------------------------------------
# Tool Call Result (internal)
# ---------------------------------------------------------------------------


class ToolResult(BaseModel):
    """Wrapped result of an agent tool call."""

    tool_use_id: str
    content: str
    is_error: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Repo Context (persistent, cross-PR)
# ---------------------------------------------------------------------------


class RepoContext(BaseModel):
    """Persistent repository context generated by pr-reviewer onboard."""

    repo_id: str                                        # "owner/repo"
    generated_at: str                                   # ISO 8601
    schema_version: str = "1.0"

    # Tech stack
    languages: list[str] = Field(default_factory=list)      # ["Python 3.12"]
    frameworks: list[str] = Field(default_factory=list)     # ["FastAPI", "Pydantic v2"]
    build_tool: str = ""                                    # "hatchling", "poetry"

    # Architecture
    architecture_pattern: str = ""                          # "layered", "MVC"
    architecture_notes: str = ""                            # free-form description
    entry_points: list[str] = Field(default_factory=list)   # ["src/pr_reviewer/cli.py"]
    key_modules: dict[str, str] = Field(default_factory=dict)  # {"core": "agent/reviewer.py"}

    # Conventions
    naming_conventions: str = ""        # "snake_case functions, PascalCase classes"
    error_handling_pattern: str = ""    # "exceptions bubble up to CLI boundary"
    import_style: str = ""              # "absolute imports only"
    coding_notes: str = ""              # free-form catch-all

    # Security
    security_sensitive_paths: list[str] = Field(default_factory=list)
    security_notes: str = ""

    # Testing
    test_framework: str = ""            # "pytest"
    test_structure: str = ""            # "tests/unit/, tests/integration/"
    test_conventions: list[str] = Field(default_factory=list)
    coverage_notes: str = ""

    # Review hints
    review_hints: list[str] = Field(default_factory=list)
    additional_context: str = ""
