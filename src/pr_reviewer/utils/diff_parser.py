"""Parse unified diff text into FileDiff models using the unidiff library."""

from __future__ import annotations

import io

import unidiff

from pr_reviewer.models import DiffStatus, FileDiff, Hunk, HunkLine


def parse_diff(raw_diff: str) -> list[FileDiff]:
    """Parse a unified diff string into a list of FileDiff models.

    Args:
        raw_diff: Raw unified diff text (e.g. from `git diff` or GitHub API).

    Returns:
        List of FileDiff objects, one per changed file.
    """
    if not raw_diff or not raw_diff.strip():
        return []

    try:
        patch_set = unidiff.PatchSet(io.StringIO(raw_diff))
    except Exception:
        # If unidiff can't parse (e.g. binary-only diff), return empty list.
        return []

    file_diffs: list[FileDiff] = []

    for patched_file in patch_set:
        status = _infer_status(patched_file)
        hunks = _parse_hunks(patched_file)
        additions = sum(h.source_length for h in hunks)  # rough
        deletions = patched_file.removed

        additions = patched_file.added
        deletions = patched_file.removed

        file_diffs.append(
            FileDiff(
                path=patched_file.path,
                old_path=patched_file.source_file if patched_file.is_rename else None,
                status=status,
                hunks=hunks,
                additions=additions,
                deletions=deletions,
            )
        )

    return file_diffs


def _infer_status(patched_file: unidiff.PatchedFile) -> DiffStatus:
    if patched_file.is_added_file:
        return DiffStatus.ADDED
    if patched_file.is_removed_file:
        return DiffStatus.DELETED
    if patched_file.is_rename:
        return DiffStatus.RENAMED
    return DiffStatus.MODIFIED


def _parse_hunks(patched_file: unidiff.PatchedFile) -> list[Hunk]:
    hunks: list[Hunk] = []
    for hunk in patched_file:
        lines: list[HunkLine] = []
        for line in hunk:
            if line.is_added:
                line_type = "+"
            elif line.is_removed:
                line_type = "-"
            else:
                line_type = " "
            lines.append(
                HunkLine(
                    line_type=line_type,
                    value=line.value,
                    source_line_no=line.source_line_no,
                    target_line_no=line.target_line_no,
                )
            )
        hunks.append(
            Hunk(
                source_start=hunk.source_start,
                source_length=hunk.source_length,
                target_start=hunk.target_start,
                target_length=hunk.target_length,
                section_header=hunk.section_header or "",
                lines=lines,
            )
        )
    return hunks


def diff_summary(file_diffs: list[FileDiff]) -> str:
    """Return a compact human-readable summary of changed files."""
    if not file_diffs:
        return "No changes detected."
    lines = [f"Changed {len(file_diffs)} file(s):"]
    for fd in file_diffs:
        rename_info = f" (renamed from {fd.old_path})" if fd.old_path else ""
        lines.append(
            f"  [{fd.status.value.upper():8s}] {fd.path}{rename_info}"
            f"  +{fd.additions}/-{fd.deletions}"
        )
    return "\n".join(lines)
