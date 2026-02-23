"""Unit tests for config loading."""

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from pr_reviewer.config import load_config
from pr_reviewer.models import Severity


class TestLoadConfig:
    def test_defaults(self):
        cfg = load_config(None)
        assert cfg.anthropic.model == "claude-sonnet-4-6"
        assert cfg.anthropic.max_tool_calls == 60
        assert cfg.review.min_severity_to_post == Severity.LOW
        assert cfg.review.max_inline_comments == 30

    def test_env_var_override(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-123")
        monkeypatch.setenv("PR_REVIEWER_MODEL", "claude-opus-4-6")
        monkeypatch.setenv("PR_REVIEWER_MIN_SEVERITY", "HIGH")
        cfg = load_config(None)
        assert cfg.anthropic.api_key == "test-key-123"
        assert cfg.anthropic.model == "claude-opus-4-6"
        assert cfg.review.min_severity_to_post == Severity.HIGH

    def test_yaml_file(self, tmp_path):
        config_data = {
            "anthropic": {"api_key": "yaml-key", "model": "claude-opus-4-6", "max_tool_calls": 30},
            "github": {"token": "gh-token"},
            "review": {"min_severity_to_post": "MEDIUM", "max_inline_comments": 15},
        }
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))

        cfg = load_config(str(config_file))
        assert cfg.anthropic.api_key == "yaml-key"
        assert cfg.anthropic.model == "claude-opus-4-6"
        assert cfg.anthropic.max_tool_calls == 30
        assert cfg.github.token == "gh-token"
        assert cfg.review.min_severity_to_post == Severity.MEDIUM
        assert cfg.review.max_inline_comments == 15

    def test_env_overrides_yaml(self, tmp_path, monkeypatch):
        config_data = {"anthropic": {"api_key": "yaml-key"}}
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump(config_data))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "env-key")

        cfg = load_config(str(config_file))
        assert cfg.anthropic.api_key == "env-key"

    def test_missing_file(self):
        cfg = load_config("/nonexistent/path/config.yaml")
        # Should return defaults without error
        assert cfg.anthropic.model == "claude-sonnet-4-6"

    def test_github_token_from_env(self, monkeypatch):
        monkeypatch.setenv("GITHUB_TOKEN", "gh-abc")
        cfg = load_config(None)
        assert cfg.github.token == "gh-abc"

    def test_bitbucket_creds_from_env(self, monkeypatch):
        monkeypatch.setenv("BITBUCKET_USERNAME", "alice")
        monkeypatch.setenv("BITBUCKET_APP_PASSWORD", "secret")
        cfg = load_config(None)
        assert cfg.bitbucket.username == "alice"
        assert cfg.bitbucket.app_password == "secret"
