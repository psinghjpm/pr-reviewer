"""Typer CLI: pr-reviewer review / config init."""

from __future__ import annotations

import re
import sys
from enum import Enum
from pathlib import Path
from typing import Optional

import structlog
import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from pr_reviewer.config import generate_example_config, load_config
from pr_reviewer.models import Platform, Severity
from pr_reviewer.output.poster import CommentPoster

console = Console()
app = typer.Typer(
    name="pr-reviewer",
    help="Agentic PR review tool powered by Claude.",
    add_completion=False,
)


class Backend(str, Enum):
    API = "api"                    # standalone: calls Anthropic API directly (needs ANTHROPIC_API_KEY)
    CLAUDECODE = "claudecode"      # inside Claude Code: prints a ready-to-paste prompt (free with Pro)


# ---------------------------------------------------------------------------
# URL parsing helpers
# ---------------------------------------------------------------------------

_GITHUB_PR_RE = re.compile(
    r"https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<pr>\d+)"
)
_BITBUCKET_PR_RE = re.compile(
    r"https://bitbucket\.org/(?P<workspace>[^/]+)/(?P<repo>[^/]+)/pull-requests/(?P<pr>\d+)"
)


def _parse_github_url(url: str) -> tuple[str, int] | None:
    m = _GITHUB_PR_RE.match(url)
    if m:
        return f"{m['owner']}/{m['repo']}", int(m["pr"])
    return None


def _parse_bitbucket_url(url: str) -> tuple[str, str, int] | None:
    m = _BITBUCKET_PR_RE.match(url)
    if m:
        return m["workspace"], m["repo"], int(m["pr"])
    return None


# ---------------------------------------------------------------------------
# Adapter factory
# ---------------------------------------------------------------------------

def _make_github_adapter(repo: str, config):  # type: ignore[no-untyped-def]
    from pr_reviewer.platforms.github_adapter import GitHubAdapter
    from pr_reviewer.utils.cache import ReviewCache

    token = config.github.token
    if not token:
        console.print("[red]Error:[/red] GitHub token not set. Use GITHUB_TOKEN env var or config.yaml.")
        raise typer.Exit(1)

    cache = ReviewCache(directory=config.cache.directory, ttl=config.cache.ttl_seconds)
    return GitHubAdapter(token=token, repo_full_name=repo, cache=cache)


def _make_bitbucket_adapter(workspace: str, repo: str, config):  # type: ignore[no-untyped-def]
    from pr_reviewer.platforms.bitbucket_adapter import BitbucketAdapter
    from pr_reviewer.utils.cache import ReviewCache

    username = config.bitbucket.username
    app_password = config.bitbucket.app_password
    if not username or not app_password:
        console.print(
            "[red]Error:[/red] Bitbucket credentials not set. "
            "Use BITBUCKET_USERNAME and BITBUCKET_APP_PASSWORD env vars."
        )
        raise typer.Exit(1)

    cache = ReviewCache(directory=config.cache.directory, ttl=config.cache.ttl_seconds)
    return BitbucketAdapter(
        username=username,
        app_password=app_password,
        workspace=workspace,
        repo_slug=repo,
        cache=cache,
    )


# ---------------------------------------------------------------------------
# Backend auto-detection
# ---------------------------------------------------------------------------

def _detect_backend(cfg, explicit_backend: Backend | None) -> Backend:
    """Pick the best backend based on explicit flag and available credentials."""
    if explicit_backend is not None:
        return explicit_backend
    if cfg.anthropic.api_key:
        return Backend.API
    return Backend.CLAUDECODE


def _print_claudecode_hint(pr_url: str, dry_run: bool) -> None:
    """Print instructions for running the review inside Claude Code."""
    dry_flag = " --dry-run" if dry_run else ""
    console.print(
        Panel(
            f"[bold]No ANTHROPIC_API_KEY found.[/bold] Run this review free with your "
            f"[cyan]Claude Pro[/cyan] subscription inside [bold]Claude Code[/bold]:\n\n"
            f"  1. Open Claude Code in any terminal\n"
            f"  2. Type:  [bold green]/pr-review {pr_url}{dry_flag}[/bold green]\n\n"
            f"The [cyan]/pr-review[/cyan] slash command is already installed at "
            f"[dim]~/.claude/commands/pr-review.md[/dim]\n\n"
            f"[dim]Or set ANTHROPIC_API_KEY to use the standalone API backend.[/dim]",
            title="💡 Use Claude Code (Free with Pro)",
            border_style="yellow",
        )
    )


# ---------------------------------------------------------------------------
# review command
# ---------------------------------------------------------------------------

@app.command()
def review(
    url: Optional[str] = typer.Option(None, "--url", help="Full PR URL (auto-detects platform)."),
    platform: Optional[str] = typer.Option(None, "--platform", help="github | bitbucket"),
    repo: Optional[str] = typer.Option(None, "--repo", help="owner/repo (GitHub) or repo-slug (Bitbucket)"),
    pr: Optional[int] = typer.Option(None, "--pr", help="PR / pull-request ID"),
    workspace: Optional[str] = typer.Option(None, "--workspace", help="Bitbucket workspace slug"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print findings without posting comments"),
    model: Optional[str] = typer.Option(None, "--model", help="Claude model override"),
    max_tool_calls: Optional[int] = typer.Option(None, "--max-tool-calls", help="Tool call budget (default 60)"),
    config_path: Optional[str] = typer.Option(None, "--config", help="Path to config.yaml"),
    backend: Optional[Backend] = typer.Option(
        None,
        "--backend",
        help=(
            "api = call Anthropic API directly (needs ANTHROPIC_API_KEY). "
            "claudecode = print /pr-review prompt for use inside Claude Code (free with Pro). "
            "Auto-detected if not set."
        ),
    ),
) -> None:
    """Run an agentic code review on a pull request.

    Works in two modes:\n
      --backend api        Use Anthropic API directly (needs ANTHROPIC_API_KEY)\n
      --backend claudecode  Print the /pr-review prompt for Claude Code (free with Claude Pro)
    """
    structlog.configure(
        processors=[
            structlog.stdlib.add_log_level,
            structlog.dev.ConsoleRenderer(),
        ]
    )

    cfg = load_config(config_path)

    # Resolve platform + repo + pr from URL or explicit flags
    resolved_platform: str | None = platform
    resolved_repo: str | None = repo
    resolved_pr: int | None = pr
    resolved_workspace: str | None = workspace
    resolved_url: str = url or ""

    if url:
        gh = _parse_github_url(url)
        bb = _parse_bitbucket_url(url)
        if gh:
            resolved_platform = "github"
            resolved_repo, resolved_pr = gh
        elif bb:
            resolved_platform = "bitbucket"
            resolved_workspace, resolved_repo, resolved_pr = bb
        else:
            console.print(f"[red]Error:[/red] Could not parse PR URL: {url}")
            raise typer.Exit(1)
    elif resolved_platform and resolved_pr:
        # Reconstruct URL from parts for claudecode backend
        if resolved_platform == "github" and resolved_repo:
            resolved_url = f"https://github.com/{resolved_repo}/pull/{resolved_pr}"
        elif resolved_platform == "bitbucket" and resolved_workspace and resolved_repo:
            resolved_url = f"https://bitbucket.org/{resolved_workspace}/{resolved_repo}/pull-requests/{resolved_pr}"

    if not resolved_platform:
        console.print("[red]Error:[/red] Specify --url or --platform.")
        raise typer.Exit(1)
    if not resolved_pr:
        console.print("[red]Error:[/red] Specify --pr or --url.")
        raise typer.Exit(1)

    # Detect backend
    effective_backend = _detect_backend(cfg, backend)

    # -----------------------------------------------------------------------
    # claudecode backend: print the slash-command hint and exit
    # -----------------------------------------------------------------------
    if effective_backend == Backend.CLAUDECODE:
        _print_claudecode_hint(resolved_url or f"<PR URL>", dry_run)
        raise typer.Exit(0)

    # -----------------------------------------------------------------------
    # api backend: run the full agentic loop
    # -----------------------------------------------------------------------
    api_key = cfg.anthropic.api_key
    if not api_key:
        console.print("[red]Error:[/red] ANTHROPIC_API_KEY not set.")
        console.print("[dim]Tip: run with --backend claudecode to use Claude Code instead (free with Pro)[/dim]")
        raise typer.Exit(1)

    # Build platform adapter
    if resolved_platform == "github":
        if not resolved_repo:
            console.print("[red]Error:[/red] --repo owner/repo required for GitHub.")
            raise typer.Exit(1)
        adapter = _make_github_adapter(resolved_repo, cfg)
    elif resolved_platform == "bitbucket":
        if not resolved_workspace or not resolved_repo:
            console.print("[red]Error:[/red] --workspace and --repo required for Bitbucket.")
            raise typer.Exit(1)
        adapter = _make_bitbucket_adapter(resolved_workspace, resolved_repo, cfg)
    else:
        console.print(f"[red]Error:[/red] Unknown platform: {resolved_platform}")
        raise typer.Exit(1)

    effective_model = model or cfg.anthropic.model
    effective_max_calls = max_tool_calls or cfg.anthropic.max_tool_calls

    from pr_reviewer.agent.reviewer import PRReviewer

    reviewer = PRReviewer(
        adapter=adapter,
        api_key=api_key,
        model=effective_model,
        max_tool_calls=effective_max_calls,
        max_content_length=cfg.review.max_content_length,
    )

    console.print(
        f"[bold]Starting review[/bold] PR #{resolved_pr} "
        f"on [cyan]{resolved_platform}[/cyan] "
        f"using [cyan]{effective_model}[/cyan] "
        f"[dim](api backend)[/dim]"
        + (" [yellow](dry run)[/yellow]" if dry_run else "")
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        task = progress.add_task("Running agentic review…", total=None)
        session = reviewer.review(resolved_pr)
        progress.update(task, description="Posting comments…")

    _print_findings_table(session.findings)

    poster = CommentPoster(
        adapter=adapter,
        min_severity=cfg.review.min_severity_to_post,
        max_inline_comments=cfg.review.max_inline_comments,
        dry_run=dry_run,
    )
    stats = poster.post(session)

    console.print("\n[bold green]Review complete![/bold green]")
    console.print(
        f"  Findings: {stats['total_findings']} total, "
        f"{stats['posted_inline']} inline posted, "
        f"{stats['skipped_duplicate']} deduplicated, "
        f"{stats['skipped_low_severity']} below severity threshold"
    )


def _print_findings_table(findings: list) -> None:  # type: ignore[type-arg]
    if not findings:
        console.print("[green]No findings.[/green]")
        return

    table = Table(title="Review Findings", show_lines=True)
    table.add_column("Severity", style="bold")
    table.add_column("Category")
    table.add_column("File:Line")
    table.add_column("Message")

    sev_colors = {
        "CRITICAL": "red",
        "HIGH": "red",
        "MEDIUM": "yellow",
        "LOW": "blue",
        "INFO": "dim",
    }

    for f in sorted(findings, key=lambda x: x.severity, reverse=True):
        color = sev_colors.get(f.severity.value, "white")
        table.add_row(
            f"[{color}]{f.severity.value}[/{color}]",
            f.category.value,
            f"{f.file}:{f.line_start}",
            f.message[:80] + ("…" if len(f.message) > 80 else ""),
        )

    console.print(table)


# ---------------------------------------------------------------------------
# config subcommand
# ---------------------------------------------------------------------------

config_app = typer.Typer(name="config", help="Manage pr-reviewer configuration.")
app.add_typer(config_app)


@config_app.command("init")
def config_init(
    output: str = typer.Option("config.yaml", "--output", "-o", help="Output file path"),
) -> None:
    """Generate a starter config.yaml file."""
    content = generate_example_config()
    if not content:
        content = (
            "# pr-reviewer configuration\n"
            "# See config.example.yaml for full options.\n"
            "anthropic:\n  api_key: \"\"\n  model: claude-sonnet-4-6\n"
            "github:\n  token: \"\"\n"
            "bitbucket:\n  username: \"\"\n  app_password: \"\"\n"
        )
    Path(output).write_text(content)
    console.print(f"[green]Created[/green] {output}")
    console.print("Edit the file to add your API keys, or leave blank to use env vars.")
    console.print(
        "\n[dim]Tip: ANTHROPIC_API_KEY is only needed for --backend api. "
        "With Claude Pro, use /pr-review inside Claude Code instead.[/dim]"
    )


if __name__ == "__main__":
    app()
