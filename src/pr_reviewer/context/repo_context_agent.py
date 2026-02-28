"""Single-shot agent that generates a RepoContext by analysing the repository."""

from __future__ import annotations

import json
import textwrap
from datetime import datetime, timezone
from pathlib import PurePosixPath

import anthropic
import structlog

from pr_reviewer.models import RepoContext
from pr_reviewer.platforms.base import PlatformAdapter

logger = structlog.get_logger(__name__)

_SCHEMA_DESCRIPTION = textwrap.dedent("""
    {
      "repo_id": "<owner/repo>",
      "generated_at": "<ISO 8601 UTC timestamp>",
      "schema_version": "1.0",
      "languages": ["<e.g. Python 3.12>"],
      "frameworks": ["<e.g. FastAPI>", "<e.g. Pydantic v2>"],
      "build_tool": "<e.g. hatchling | poetry | setuptools | npm | cargo>",
      "architecture_pattern": "<e.g. layered | MVC | hexagonal | monolith>",
      "architecture_notes": "<free-form description of major layers / components>",
      "entry_points": ["<path to main CLI or app entry>"],
      "key_modules": {"<role>": "<path>"},
      "naming_conventions": "<e.g. snake_case functions, PascalCase classes>",
      "error_handling_pattern": "<e.g. exceptions bubble up to CLI boundary>",
      "import_style": "<e.g. absolute imports only>",
      "coding_notes": "<any other conventions worth knowing>",
      "security_sensitive_paths": ["<paths that handle auth, secrets, payments, etc.>"],
      "security_notes": "<known security patterns or concerns>",
      "test_framework": "<e.g. pytest | jest | go test>",
      "test_structure": "<e.g. tests/unit/, tests/integration/>",
      "test_conventions": ["<e.g. use fixtures from conftest.py>"],
      "coverage_notes": "<known coverage gaps or requirements>",
      "review_hints": ["<known gotchas a reviewer should always check>"],
      "additional_context": "<anything else relevant for code review>"
    }
""").strip()


class RepoContextAgent:
    """Generate a RepoContext via a single Claude API call.

    Deterministically gathers repo data (README, manifest, directory tree,
    sample source files), then calls Claude once to produce structured JSON.
    """

    _MANIFEST_FILES = [
        "pyproject.toml", "setup.py", "package.json", "go.mod",
        "Cargo.toml", "pom.xml", "build.gradle", "Gemfile", "requirements.txt",
    ]
    _README_PATTERNS = ["README.md", "README.rst", "README.txt", "README"]
    _SOURCE_EXTENSIONS = {".py", ".ts", ".tsx", ".js", ".go", ".java", ".rb", ".rs"}
    _MAX_SAMPLE_FILES = 8
    _MAX_FILE_CHARS = 6_000

    def __init__(
        self,
        adapter: PlatformAdapter,
        api_key: str,
        repo_full_name: str,
        model: str = "claude-sonnet-4-6",
    ) -> None:
        self._adapter = adapter
        self._client = anthropic.Anthropic(api_key=api_key)
        self._repo_full_name = repo_full_name
        self._model = model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate(self) -> RepoContext:
        """Analyse the repository and return a populated RepoContext."""
        logger.info("repo_context_generate_start", repo=self._repo_full_name)

        repo_files = self._list_all_files()

        readme = self._fetch_first_match(repo_files, self._README_PATTERNS)
        manifest = self._fetch_first_match(repo_files, self._MANIFEST_FILES)
        dir_tree = self._build_dir_tree(repo_files)
        samples = self._sample_source_files(repo_files)

        prompt = self._build_prompt(readme, manifest, dir_tree, samples)

        try:
            response = self._client.messages.create(
                model=self._model,
                max_tokens=4096,
                messages=[{"role": "user", "content": prompt}],
            )
            raw_text = response.content[0].text
        except Exception as exc:
            logger.exception("repo_context_api_error", error=str(exc))
            raw_text = ""

        return self._parse_response(raw_text)

    # ------------------------------------------------------------------
    # Data gathering helpers
    # ------------------------------------------------------------------

    def _list_all_files(self) -> list[str]:
        """Collect all repo files across common source extensions + manifests."""
        all_files: list[str] = []
        patterns = ["**/*.py", "**/*.ts", "**/*.tsx", "**/*.js", "**/*.go",
                    "**/*.java", "**/*.rb", "**/*.rs", "**/*.md", "**/*.toml",
                    "**/*.json", "**/*.yaml", "**/*.yml"]
        seen: set[str] = set()
        for pattern in patterns:
            try:
                for f in self._adapter.list_repo_files("HEAD", pattern):
                    if f not in seen:
                        seen.add(f)
                        all_files.append(f)
            except Exception:
                pass
        return all_files

    def _fetch_first_match(self, repo_files: list[str], candidates: list[str]) -> str:
        """Return content of the first candidate found in repo_files, or ''."""
        file_set = set(repo_files)
        for candidate in candidates:
            if candidate in file_set:
                try:
                    content = self._adapter.get_file_content(candidate, "HEAD")
                    return content[: self._MAX_FILE_CHARS]
                except Exception:
                    pass
        return ""

    def _build_dir_tree(self, repo_files: list[str]) -> str:
        """Build a top-2-level directory tree string from the file list."""
        dirs: set[str] = set()
        for path in repo_files:
            parts = PurePosixPath(path).parts
            if len(parts) >= 1:
                dirs.add(parts[0])
            if len(parts) >= 2:
                dirs.add(f"{parts[0]}/{parts[1]}")
        return "\n".join(sorted(dirs))

    def _sample_source_files(self, repo_files: list[str]) -> dict[str, str]:
        """Return up to _MAX_SAMPLE_FILES representative source files."""
        # Filter to source extensions, skip tests and migrations
        _skip_keywords = {"test", "migration", "alembic", "__pycache__", "node_modules",
                          "dist", "build", ".git", "vendor"}

        candidates = [
            f for f in repo_files
            if PurePosixPath(f).suffix in self._SOURCE_EXTENSIONS
            and not any(kw in f.lower() for kw in _skip_keywords)
            and PurePosixPath(f).name not in {"__init__.py", "conftest.py"}
        ]

        # Prefer files in src/, lib/, app/ directories first
        def _priority(path: str) -> int:
            p = path.lower()
            if p.startswith(("src/", "lib/", "app/")):
                return 0
            return 1

        candidates.sort(key=_priority)
        selected = candidates[: self._MAX_SAMPLE_FILES]

        result: dict[str, str] = {}
        for path in selected:
            try:
                content = self._adapter.get_file_content(path, "HEAD")
                result[path] = content[: self._MAX_FILE_CHARS]
            except Exception:
                pass
        return result

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        readme: str,
        manifest: str,
        dir_tree: str,
        samples: dict[str, str],
    ) -> str:
        sections: list[str] = [
            f"You are analysing the repository `{self._repo_full_name}` to produce structured "
            "context that will make future code reviews more accurate and convention-aware.\n",
        ]

        if readme:
            sections.append(f"## README\n```\n{readme}\n```\n")

        if manifest:
            sections.append(f"## Build Manifest\n```\n{manifest}\n```\n")

        if dir_tree:
            sections.append(f"## Directory Tree (top 2 levels)\n```\n{dir_tree}\n```\n")

        if samples:
            sections.append("## Representative Source Files\n")
            for path, content in samples.items():
                sections.append(f"### {path}\n```\n{content}\n```\n")

        sections.append(
            "## Task\n"
            "Based on all of the above, produce a JSON object matching EXACTLY this schema "
            "(fill every field you can infer; use empty string or empty list for unknown fields):\n\n"
            f"```json\n{_SCHEMA_DESCRIPTION}\n```\n\n"
            f"Set `repo_id` to `{self._repo_full_name}` and `generated_at` to "
            f"`{datetime.now(timezone.utc).isoformat()}`.\n\n"
            "Return ONLY the JSON object — no markdown fences, no explanation."
        )

        return "\n".join(sections)

    # ------------------------------------------------------------------
    # Response parsing
    # ------------------------------------------------------------------

    def _parse_response(self, raw: str) -> RepoContext:
        """Parse Claude's response into a RepoContext; fall back to minimal on error."""
        fallback = RepoContext(
            repo_id=self._repo_full_name,
            generated_at=datetime.now(timezone.utc).isoformat(),
        )
        if not raw:
            return fallback

        text = raw.strip()
        # Strip markdown fences if present
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove opening fence (possibly ```json)
            lines = lines[1:]
            # Remove closing fence
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            data = json.loads(text)
            # Ensure required fields are present
            data.setdefault("repo_id", self._repo_full_name)
            data.setdefault("generated_at", datetime.now(timezone.utc).isoformat())
            return RepoContext.model_validate(data)
        except Exception as exc:
            logger.warning("repo_context_parse_error", error=str(exc))
            return fallback
