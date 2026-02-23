"""Post inline and summary comments via the platform adapter."""

from __future__ import annotations

import structlog

from pr_reviewer.models import AgentSession, ReviewFinding, Severity
from pr_reviewer.output.deduplicator import Deduplicator
from pr_reviewer.output.formatter import format_inline_comment, format_summary_comment
from pr_reviewer.platforms.base import PlatformAdapter

logger = structlog.get_logger(__name__)


class CommentPoster:
    """Filter, deduplicate, and post review findings to the PR."""

    def __init__(
        self,
        adapter: PlatformAdapter,
        min_severity: Severity = Severity.LOW,
        max_inline_comments: int = 30,
        dry_run: bool = False,
    ) -> None:
        self._adapter = adapter
        self._min_severity = min_severity
        self._max_inline = max_inline_comments
        self._dry_run = dry_run

    def post(self, session: AgentSession) -> dict:
        """Post all findings and the summary comment for *session*.

        Returns a dict with counts: posted_inline, skipped_low_severity,
        skipped_duplicate, posted_summary.
        """
        pr_id = session.pr_metadata.pr_id

        # Fetch existing comments for dedup
        try:
            existing = self._adapter.get_existing_comments(pr_id)
        except Exception as exc:
            logger.warning("existing_comments_fetch_failed", error=str(exc))
            existing = []

        deduplicator = Deduplicator(existing)

        # Filter by severity
        filtered = self._filter_by_severity(session.findings)
        skipped_severity = len(session.findings) - len(filtered)

        # Dedup
        unique = deduplicator.filter_findings(filtered, format_inline_comment)
        skipped_dup = len(filtered) - len(unique)

        # Cap inline comments
        to_post = unique[: self._max_inline]
        skipped_cap = len(unique) - len(to_post)

        # Sort: CRITICAL first
        to_post = sorted(to_post, key=lambda f: f.severity, reverse=True)

        # Post inline comments
        posted_inline = 0
        for finding in to_post:
            body = format_inline_comment(finding)
            if self._dry_run:
                logger.info(
                    "dry_run_inline",
                    file=finding.file,
                    line=finding.line_start,
                    severity=finding.severity,
                )
            else:
                try:
                    self._adapter.post_inline_comment(
                        pr_id, finding.file, finding.line_start, body
                    )
                    posted_inline += 1
                except Exception as exc:
                    logger.error(
                        "inline_comment_failed",
                        file=finding.file,
                        line=finding.line_start,
                        error=str(exc),
                    )

        # Post summary
        posted_summary = 0
        if session.summary:
            summary_body = format_summary_comment(session.summary, session.findings)
            if self._dry_run:
                logger.info("dry_run_summary", length=len(summary_body))
                print("\n" + "=" * 60)
                print("PR REVIEW SUMMARY (dry run)")
                print("=" * 60)
                print(summary_body)
            else:
                try:
                    self._adapter.post_pr_summary(pr_id, summary_body)
                    posted_summary = 1
                except Exception as exc:
                    logger.error("summary_comment_failed", error=str(exc))

        stats = {
            "total_findings": len(session.findings),
            "posted_inline": posted_inline,
            "skipped_low_severity": skipped_severity,
            "skipped_duplicate": skipped_dup,
            "skipped_cap": skipped_cap,
            "posted_summary": posted_summary,
            "dry_run": self._dry_run,
        }
        logger.info("posting_complete", **stats)
        return stats

    def _filter_by_severity(self, findings: list[ReviewFinding]) -> list[ReviewFinding]:
        """Return findings at or above the configured minimum severity."""
        order = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
        min_idx = order.index(self._min_severity)
        return [f for f in findings if order.index(f.severity) >= min_idx]
