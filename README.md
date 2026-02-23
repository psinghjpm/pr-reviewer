# pr-reviewer

Agentic Pull Request review tool powered by Claude — rivals CodeRabbit, Qodo, Greptile, and Graphite.

## Features

- **Multi-platform**: GitHub and Bitbucket Cloud
- **Context-aware**: tree-sitter AST dependency tracing, symbol search, test file discovery
- **Agentic**: Claude-powered review loop with up to 60 tool calls per review
- **5 review passes**: Intent → Context → Logic & Bugs → Security → Tests
- **CI/CD ready**: GitHub Actions and Bitbucket Pipelines included

## Quick Start

```bash
pip install pr-reviewer

# GitHub PR
GITHUB_TOKEN=... ANTHROPIC_API_KEY=... \
  pr-reviewer review --url https://github.com/owner/repo/pull/123

# Bitbucket PR
BITBUCKET_USERNAME=... BITBUCKET_APP_PASSWORD=... ANTHROPIC_API_KEY=... \
  pr-reviewer review --platform bitbucket --workspace ws --repo repo --pr 1

# Dry run (no comments posted)
pr-reviewer review --url https://github.com/owner/repo/pull/123 --dry-run

# Generate config
pr-reviewer config init
```

## Configuration

```bash
pr-reviewer config init  # creates config.yaml
```

See `config.example.yaml` for all options. Environment variables always override config file values.

## Running Tests

```bash
# Unit tests (no network)
pytest tests/unit/ -v

# Integration tests (mock adapter, no network)
pytest tests/integration/ -v

# E2E tests (live network, posts real comments)
RUN_E2E=1 E2E_GITHUB_REPO=owner/repo E2E_GITHUB_PR=1 pytest tests/e2e/ -v
```
