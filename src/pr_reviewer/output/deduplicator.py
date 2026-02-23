"""Suppress already-posted PR comments using fingerprint and semantic matching."""

from __future__ import annotations

from pr_reviewer.models import ReviewFinding

_SEMANTIC_WORD_THRESHOLD = 5  # min shared significant words to consider a duplicate


def _fingerprint(path: str, line: int, body: str) -> str:
    return f"{path}:{line}:{body[:80]}"


def _significant_words(text: str) -> set[str]:
    """Return the set of 'significant' words (length > 3, not stop words)."""
    stop = {
        "the", "and", "for", "are", "was", "this", "that", "with",
        "have", "from", "they", "will", "been", "has", "not", "but",
        "what", "all", "were", "when", "your", "can", "said", "each",
    }
    words = set(w.lower() for w in text.split() if len(w) > 3 and w.lower() not in stop)
    return words


class Deduplicator:
    """Filter out ReviewFindings that already exist as PR comments.

    Two strategies:
    1. **Fingerprint** match: exact ``{file}:{line}:{body[:80]}`` match.
    2. **Semantic** match: 5+ significant words in common at the same location.
    """

    def __init__(self, existing_comments: list[dict]) -> None:
        self._fingerprints: set[str] = set()
        self._semantic_index: list[tuple[str, int, set[str]]] = []  # (path, line, words)

        for c in existing_comments:
            path = c.get("path", "")
            line = int(c.get("line") or 0)
            body = c.get("body", "")
            self._fingerprints.add(_fingerprint(path, line, body))
            self._semantic_index.append((path, line, _significant_words(body)))

    def is_duplicate(self, finding: ReviewFinding, comment_body: str) -> bool:
        """Return True if this finding is already covered by an existing comment."""
        fp = _fingerprint(finding.file, finding.line_start, comment_body)
        if fp in self._fingerprints:
            return True

        # Semantic check
        finding_words = _significant_words(finding.message)
        for path, line, existing_words in self._semantic_index:
            if path != finding.file:
                continue
            if abs(line - finding.line_start) > 5:
                continue
            shared = finding_words & existing_words
            if len(shared) >= _SEMANTIC_WORD_THRESHOLD:
                return True

        return False

    def filter_findings(
        self, findings: list[ReviewFinding], format_fn: "Any"
    ) -> list[ReviewFinding]:
        """Return only non-duplicate findings.

        Args:
            findings: All findings from the agent session.
            format_fn: Callable(finding) → str that produces the comment body.
        """
        unique: list[ReviewFinding] = []
        for f in findings:
            body = format_fn(f)
            if not self.is_duplicate(f, body):
                unique.append(f)
        return unique
