"""Integration tests for RepoContextAgent — mock adapter, no network."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from pr_reviewer.context.repo_context_agent import RepoContextAgent
from pr_reviewer.models import RepoContext


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_VALID_CONTEXT_JSON = json.dumps({
    "repo_id": "owner/repo",
    "generated_at": "2026-01-01T00:00:00+00:00",
    "schema_version": "1.0",
    "languages": ["Python 3.12"],
    "frameworks": ["FastAPI", "Pydantic v2"],
    "build_tool": "hatchling",
    "architecture_pattern": "layered",
    "architecture_notes": "CLI → agent → platform adapters",
    "entry_points": ["src/pr_reviewer/cli.py"],
    "key_modules": {"core": "src/pr_reviewer/agent/reviewer.py"},
    "naming_conventions": "snake_case functions, PascalCase classes",
    "error_handling_pattern": "exceptions bubble up to CLI boundary",
    "import_style": "absolute imports only",
    "coding_notes": "",
    "security_sensitive_paths": ["src/pr_reviewer/config.py"],
    "security_notes": "API keys loaded from env vars",
    "test_framework": "pytest",
    "test_structure": "tests/unit/, tests/integration/",
    "test_conventions": ["use fixtures from conftest.py"],
    "coverage_notes": "",
    "review_hints": ["Always validate returncode in _run()"],
    "additional_context": "",
})


@pytest.fixture
def mock_anthropic(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Patch anthropic.Anthropic so no real API calls are made."""
    client = MagicMock()
    client.messages.create.return_value = MagicMock(
        content=[MagicMock(text=_VALID_CONTEXT_JSON)]
    )
    monkeypatch.setattr(
        "pr_reviewer.context.repo_context_agent.anthropic.Anthropic",
        lambda **kw: client,
    )
    return client


@pytest.fixture
def mock_adapter_with_files(mock_adapter: MagicMock) -> MagicMock:
    """Extend the shared mock_adapter with repo-file data."""
    mock_adapter.list_repo_files.return_value = [
        "README.md",
        "pyproject.toml",
        "src/pr_reviewer/cli.py",
        "src/pr_reviewer/agent/reviewer.py",
        "tests/unit/test_something.py",
    ]
    mock_adapter.get_file_content.side_effect = lambda path, ref: {
        "README.md": "# pr-reviewer\nAgentic PR review tool.\n",
        "pyproject.toml": '[project]\nname = "pr-reviewer"\n',
        "src/pr_reviewer/cli.py": "# CLI entry point\nimport typer\n",
        "src/pr_reviewer/agent/reviewer.py": "# Core review loop\nimport anthropic\n",
        "tests/unit/test_something.py": "# test\nimport pytest\n",
    }.get(path, "")
    return mock_adapter


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_generate_returns_repo_context(
    mock_anthropic: MagicMock,
    mock_adapter_with_files: MagicMock,
) -> None:
    agent = RepoContextAgent(
        adapter=mock_adapter_with_files,
        api_key="test-key",
        repo_full_name="owner/repo",
    )
    result = agent.generate()

    assert isinstance(result, RepoContext)
    assert result.repo_id == "owner/repo"
    assert "Python 3.12" in result.languages
    assert result.test_framework == "pytest"
    assert mock_anthropic.messages.create.called


def test_generate_handles_missing_readme(
    mock_anthropic: MagicMock,
    mock_adapter: MagicMock,
) -> None:
    """Agent should still work when no README is in the file list."""
    mock_adapter.list_repo_files.return_value = ["pyproject.toml", "src/main.py"]
    mock_adapter.get_file_content.side_effect = lambda path, ref: {
        "pyproject.toml": '[project]\nname = "myapp"\n',
        "src/main.py": "def main(): pass\n",
    }.get(path, "")

    agent = RepoContextAgent(
        adapter=mock_adapter,
        api_key="test-key",
        repo_full_name="owner/repo",
    )
    result = agent.generate()

    assert isinstance(result, RepoContext)
    assert result.repo_id == "owner/repo"


def test_generate_handles_json_parse_error(
    monkeypatch: pytest.MonkeyPatch,
    mock_adapter: MagicMock,
) -> None:
    """When Claude returns garbage, agent returns a minimal RepoContext without raising."""
    client = MagicMock()
    client.messages.create.return_value = MagicMock(
        content=[MagicMock(text="not json at all ¯\\_(ツ)_/¯")]
    )
    monkeypatch.setattr(
        "pr_reviewer.context.repo_context_agent.anthropic.Anthropic",
        lambda **kw: client,
    )

    mock_adapter.list_repo_files.return_value = []

    agent = RepoContextAgent(
        adapter=mock_adapter,
        api_key="test-key",
        repo_full_name="owner/repo",
    )
    result = agent.generate()

    assert isinstance(result, RepoContext)
    assert result.repo_id == "owner/repo"
    # Minimal fallback — all optional fields default to empty
    assert result.languages == []
    assert result.test_framework == ""


def test_generate_handles_api_error(
    monkeypatch: pytest.MonkeyPatch,
    mock_adapter: MagicMock,
) -> None:
    """When the API call itself raises, agent returns a minimal RepoContext."""
    client = MagicMock()
    client.messages.create.side_effect = RuntimeError("network error")
    monkeypatch.setattr(
        "pr_reviewer.context.repo_context_agent.anthropic.Anthropic",
        lambda **kw: client,
    )

    mock_adapter.list_repo_files.return_value = []

    agent = RepoContextAgent(
        adapter=mock_adapter,
        api_key="test-key",
        repo_full_name="owner/repo",
    )
    result = agent.generate()

    assert isinstance(result, RepoContext)
    assert result.repo_id == "owner/repo"


def test_prompt_includes_readme_content(
    mock_anthropic: MagicMock,
    mock_adapter: MagicMock,
) -> None:
    """The prompt sent to Claude should include the README content."""
    mock_adapter.list_repo_files.return_value = ["README.md"]
    mock_adapter.get_file_content.return_value = "# MyProject — the best tool ever\n"

    agent = RepoContextAgent(
        adapter=mock_adapter,
        api_key="test-key",
        repo_full_name="owner/repo",
    )
    agent.generate()

    assert mock_anthropic.messages.create.called
    call_kwargs = mock_anthropic.messages.create.call_args
    messages = call_kwargs[1]["messages"] if call_kwargs[1] else call_kwargs[0][1]
    prompt_text = messages[0]["content"]
    assert "MyProject" in prompt_text
    assert "README" in prompt_text


def test_generate_strips_markdown_fences(
    monkeypatch: pytest.MonkeyPatch,
    mock_adapter: MagicMock,
) -> None:
    """Agent should strip ```json ... ``` fences from Claude's response."""
    fenced = f"```json\n{_VALID_CONTEXT_JSON}\n```"
    client = MagicMock()
    client.messages.create.return_value = MagicMock(
        content=[MagicMock(text=fenced)]
    )
    monkeypatch.setattr(
        "pr_reviewer.context.repo_context_agent.anthropic.Anthropic",
        lambda **kw: client,
    )

    mock_adapter.list_repo_files.return_value = []

    agent = RepoContextAgent(
        adapter=mock_adapter,
        api_key="test-key",
        repo_full_name="owner/repo",
    )
    result = agent.generate()

    assert isinstance(result, RepoContext)
    assert result.repo_id == "owner/repo"
    assert result.test_framework == "pytest"
