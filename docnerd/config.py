"""Configuration loading with environment variable overrides."""

import os
from pathlib import Path
from typing import Any

import yaml


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load config from YAML file, with env var overrides."""
    if config_path is None:
        # Prefer workspace config (when running as action, cwd is the source repo)
        cwd_config = Path.cwd() / "config.yaml"
        action_config = Path(__file__).parent.parent / "config.yaml"
        config_path = cwd_config if cwd_config.exists() else action_config
    config_path = Path(config_path)

    if not config_path.exists():
        config_path = Path(__file__).parent.parent / "config.example.yaml"

    config: dict[str, Any] = {}
    if config_path.exists():
        with open(config_path) as f:
            config = yaml.safe_load(f) or {}

    # Environment variable overrides
    config.setdefault("source_repo", {})
    config.setdefault("target_repo", {})
    config.setdefault("llm", {})

    if os.getenv("GITHUB_TOKEN"):
        config["source_repo"]["token"] = os.getenv("GITHUB_TOKEN")
    if os.getenv("SOURCE_REPO_TOKEN"):
        config["source_repo"]["token"] = os.getenv("SOURCE_REPO_TOKEN")
    if os.getenv("TARGET_REPO_TOKEN"):
        config["target_repo"]["token"] = os.getenv("TARGET_REPO_TOKEN")
    if os.getenv("ANTHROPIC_API_KEY"):
        config["llm"]["api_key"] = os.getenv("ANTHROPIC_API_KEY")

    # Target repo can be overridden by env
    if os.getenv("TARGET_REPO_OWNER"):
        config["target_repo"]["owner"] = os.getenv("TARGET_REPO_OWNER")
    if os.getenv("TARGET_REPO_NAME"):
        config["target_repo"]["name"] = os.getenv("TARGET_REPO_NAME")

    return config
