---
name: repo-onboard
description: "Analyse the current repository and generate a RepoContext JSON file for richer, convention-aware PR reviews. Usage: /repo-onboard [--output <path>] [--force]"
---

Analyse the current git repository and generate a `repo_context.json` file: $ARGUMENTS

---

## Parse Arguments

Extract from `$ARGUMENTS`:
- `OUTPUT`: custom output path for `repo_context.json` (optional)
- `FORCE`: true if `--force` is present (overwrite existing context, default: false)

---

## Phase 1 — Detect Repo Identity

```bash
git remote get-url origin
git log --oneline -20
git rev-parse --show-toplevel
```

Parse `OWNER` and `REPO` from the remote URL:
- GitHub SSH: `git@github.com:OWNER/REPO.git`
- GitHub HTTPS: `https://github.com/OWNER/REPO.git`
- If no remote, use the directory name as `REPO` and `local` as `OWNER`.

Determine `SAVE_PATH`:
- If `OUTPUT` arg given, use that path.
- Otherwise: `~/.pr-reviewer/contexts/<OWNER>/<REPO>/repo_context.json`

If `SAVE_PATH` already exists and `FORCE` is false:
```
⚠️  Repo context already exists at <SAVE_PATH>.
    Pass --force to overwrite.
```
Stop here.

---

## Phase 2 — Read Key Files

Read the following if they exist (skip silently if absent):

```bash
cat README.md 2>/dev/null || cat README.rst 2>/dev/null || cat README.txt 2>/dev/null
cat pyproject.toml 2>/dev/null || cat setup.py 2>/dev/null || \
  cat package.json 2>/dev/null || cat go.mod 2>/dev/null || \
  cat Cargo.toml 2>/dev/null || cat requirements.txt 2>/dev/null
ls .github/ 2>/dev/null
cat .github/workflows/*.yml 2>/dev/null | head -100
```

---

## Phase 3 — Sample Source Files

List source files and read 5–8 representative ones from core, non-test directories:

**Python:**
```bash
git ls-files '*.py' | grep -v test | grep -v migration | head -30
```

**TypeScript / JavaScript:**
```bash
git ls-files '*.ts' '*.tsx' | grep -v '\.test\.' | grep -v '__tests__' | head -30
```

**Go:**
```bash
git ls-files '*.go' | grep -v '_test\.go' | head -30
```

Pick 5–8 files from core module directories (src/, lib/, app/, pkg/, cmd/).
Prefer files that define primary abstractions (not `__init__.py`, not config files).
Read each selected file.

---

## Phase 4 — Analyse and Produce JSON

Based on everything you have read, infer and produce a JSON object with this exact schema:

```json
{
  "repo_id": "<owner/repo>",
  "generated_at": "<ISO 8601 UTC timestamp>",
  "schema_version": "1.0",
  "languages": ["<e.g. Python 3.12>"],
  "frameworks": ["<e.g. FastAPI>", "<e.g. Pydantic v2>"],
  "build_tool": "<e.g. hatchling | poetry | npm | cargo>",
  "architecture_pattern": "<e.g. layered | MVC | hexagonal | monolith>",
  "architecture_notes": "<description of major layers / components>",
  "entry_points": ["<path to main CLI or app entry>"],
  "key_modules": {"<role>": "<path>"},
  "naming_conventions": "<e.g. snake_case functions, PascalCase classes>",
  "error_handling_pattern": "<e.g. exceptions bubble up to CLI boundary>",
  "import_style": "<e.g. absolute imports only>",
  "coding_notes": "<any other coding conventions worth knowing>",
  "security_sensitive_paths": ["<paths handling auth, secrets, payments>"],
  "security_notes": "<known security patterns or areas of concern>",
  "test_framework": "<e.g. pytest | jest | go test>",
  "test_structure": "<e.g. tests/unit/, tests/integration/>",
  "test_conventions": ["<e.g. use fixtures from conftest.py>"],
  "coverage_notes": "<known coverage gaps or requirements>",
  "review_hints": ["<known codebase gotchas a reviewer should always check>"],
  "additional_context": "<anything else useful for code review>"
}
```

Guidelines:
- Use empty string `""` or empty list `[]` for fields you cannot confidently infer.
- `repo_id` must be `OWNER/REPO`.
- `generated_at` must be the current UTC time in ISO 8601 format.
- `review_hints` should capture real, codebase-specific pitfalls — not generic advice.
- `security_sensitive_paths` should list actual paths (e.g. `src/auth/`, `config/secrets.py`).

**Edge cases:**
- **Bare repo / no remote:** Set `repo_id` to the directory name, note in `additional_context`.
- **Monorepo:** Warn the user and suggest scoping with
  `--output ./service-a/.pr-reviewer/repo_context.json` for per-service context.

---

## Phase 5 — Write the File

```bash
mkdir -p "$(dirname "$SAVE_PATH")"
```

Write the JSON to `SAVE_PATH`. Ensure it is valid JSON (no trailing commas, no comments).

---

## Phase 6 — Confirm

Print a human-readable summary of what was captured:

```
✅ Repo context generated for <OWNER>/<REPO>
   Saved to: <SAVE_PATH>

   Tech Stack:        <languages + frameworks>
   Build Tool:        <build_tool>
   Architecture:      <architecture_pattern>
   Test Framework:    <test_framework>
   Security Paths:    <count> path(s) identified
   Review Hints:      <count> hint(s) captured

To use in reviews:    /pr-review <PR-URL>   (Phase 0 loads this automatically)
To refresh context:   /repo-onboard --force
```

If the file was written to the global path (`~/.pr-reviewer/...`), note that it will be used
automatically for all future reviews of this repo from any working directory.

If it was written to a local path (`.pr-reviewer/repo_context.json`), note that committing it
allows the whole team to benefit from consistent review context.
