"""Persistent storage for RepoContext objects."""

from __future__ import annotations

from pathlib import Path

from pr_reviewer.models import RepoContext


class RepoContextStore:
    """Save, load, and locate RepoContext JSON files.

    Storage hierarchy (local takes precedence):
      Local:  <repo_root>/.pr-reviewer/repo_context.json  (committable; team-shareable)
      Global: ~/.pr-reviewer/contexts/<owner>/<repo>/repo_context.json
    """

    @staticmethod
    def local_path(repo_root: Path) -> Path:
        return repo_root / ".pr-reviewer" / "repo_context.json"

    @staticmethod
    def global_path(repo_full_name: str) -> Path:
        parts = repo_full_name.split("/", 1)
        owner = parts[0]
        repo = parts[1] if len(parts) > 1 else "unknown"
        return Path.home() / ".pr-reviewer" / "contexts" / owner / repo / "repo_context.json"

    @staticmethod
    def find(repo_full_name: str, local_root: Path | None = None) -> RepoContext | None:
        """Check local path first, then global. Returns None if neither exists."""
        if local_root is not None:
            local = RepoContextStore.local_path(local_root)
            if local.exists():
                return RepoContextStore.load(local)
        return RepoContextStore.load(RepoContextStore.global_path(repo_full_name))

    @staticmethod
    def save(context: RepoContext, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(context.model_dump_json(indent=2), encoding="utf-8")

    @staticmethod
    def load(path: Path) -> RepoContext | None:
        if not path.exists():
            return None
        try:
            return RepoContext.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:
            return None
