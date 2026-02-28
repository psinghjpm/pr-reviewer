"""Unit tests for RepoContextStore — pure, no network."""

from __future__ import annotations

from pathlib import Path

import pytest

from pr_reviewer.context.repo_context_store import RepoContextStore
from pr_reviewer.models import RepoContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(repo_id: str = "owner/repo") -> RepoContext:
    return RepoContext(
        repo_id=repo_id,
        generated_at="2026-01-01T00:00:00+00:00",
        languages=["Python 3.12"],
        frameworks=["FastAPI"],
        test_framework="pytest",
        review_hints=["Always check edge cases in payment flow"],
    )


# ---------------------------------------------------------------------------
# Path construction
# ---------------------------------------------------------------------------

def test_local_path(tmp_path: Path) -> None:
    result = RepoContextStore.local_path(tmp_path)
    assert result == tmp_path / ".pr-reviewer" / "repo_context.json"


def test_global_path() -> None:
    result = RepoContextStore.global_path("acme/myrepo")
    assert result.parts[-1] == "repo_context.json"
    assert result.parts[-2] == "myrepo"
    assert result.parts[-3] == "acme"
    assert result.parts[-4] == "contexts"
    assert result.parts[-5] == ".pr-reviewer"


def test_global_path_no_slash() -> None:
    """When repo_full_name has no '/', owner = full string, repo = 'unknown'."""
    result = RepoContextStore.global_path("justowner")
    assert result.parts[-2] == "unknown"
    assert result.parts[-3] == "justowner"


# ---------------------------------------------------------------------------
# Save / load round-trip
# ---------------------------------------------------------------------------

def test_save_and_load_roundtrip(tmp_path: Path) -> None:
    ctx = _make_context()
    path = tmp_path / "ctx.json"

    RepoContextStore.save(ctx, path)
    assert path.exists()

    loaded = RepoContextStore.load(path)
    assert loaded is not None
    assert loaded.repo_id == ctx.repo_id
    assert loaded.languages == ctx.languages
    assert loaded.review_hints == ctx.review_hints


def test_save_creates_parent_dirs(tmp_path: Path) -> None:
    ctx = _make_context()
    path = tmp_path / "deep" / "nested" / "ctx.json"

    RepoContextStore.save(ctx, path)
    assert path.exists()


# ---------------------------------------------------------------------------
# Load edge cases
# ---------------------------------------------------------------------------

def test_load_missing_returns_none(tmp_path: Path) -> None:
    result = RepoContextStore.load(tmp_path / "nonexistent.json")
    assert result is None


def test_load_corrupted_returns_none(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{ this is not valid json !!!", encoding="utf-8")
    result = RepoContextStore.load(bad)
    assert result is None


def test_load_empty_file_returns_none(tmp_path: Path) -> None:
    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    result = RepoContextStore.load(empty)
    assert result is None


# ---------------------------------------------------------------------------
# find() — precedence
# ---------------------------------------------------------------------------

def test_find_prefers_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    local_ctx = _make_context("local/repo")
    global_ctx = _make_context("global/repo")

    # Write local context
    local_path = RepoContextStore.local_path(tmp_path)
    RepoContextStore.save(local_ctx, local_path)

    # Write global context in a fake home
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    global_path = RepoContextStore.global_path("owner/repo")
    RepoContextStore.save(global_ctx, global_path)

    result = RepoContextStore.find("owner/repo", local_root=tmp_path)
    assert result is not None
    assert result.repo_id == "local/repo"


def test_find_falls_back_to_global(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    global_ctx = _make_context("global/repo")

    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))
    global_path = RepoContextStore.global_path("owner/repo")
    RepoContextStore.save(global_ctx, global_path)

    # local_root points to a dir without .pr-reviewer/
    result = RepoContextStore.find("owner/repo", local_root=tmp_path)
    assert result is not None
    assert result.repo_id == "global/repo"


def test_find_returns_none_when_both_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake_home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", staticmethod(lambda: fake_home))

    result = RepoContextStore.find("owner/repo", local_root=tmp_path)
    assert result is None
