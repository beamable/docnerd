"""Fetch existing documentation from the target docs repository."""

import base64
from typing import Any

import yaml
from github.Repository import Repository


def get_mkdocs_config(repo: Repository, ref: str) -> dict[str, Any]:
    """Get mkdocs.yml config. Returns empty dict if not found or invalid."""
    try:
        content = repo.get_contents("mkdocs.yml", ref=ref)
        if content.content:
            raw = base64.b64decode(content.content).decode("utf-8", errors="replace")
            return yaml.safe_load(raw) or {}
    except Exception:
        pass
    try:
        content = repo.get_contents("mkdocs.yaml", ref=ref)
        if content.content:
            raw = base64.b64decode(content.content).decode("utf-8", errors="replace")
            return yaml.safe_load(raw) or {}
    except Exception:
        pass
    return {}


def get_docs_dir(config: dict[str, Any]) -> str:
    """Extract docs directory from mkdocs config. Default: docs."""
    return config.get("docs_dir", "docs")


def get_nav_structure(config: dict[str, Any]) -> str:
    """Extract nav structure from mkdocs config for context."""
    nav = config.get("nav", [])
    if not nav:
        return "(no nav defined)"
    return yaml.dump(nav, default_flow_style=False, allow_unicode=True)


def _list_md_files(repo: Repository, path: str, ref: str) -> list[str]:
    """Recursively list all .md files under path."""
    files: list[str] = []
    try:
        contents = repo.get_contents(path, ref=ref)
        for item in contents:
            if item.type == "dir":
                files.extend(_list_md_files(repo, item.path, ref))
            elif item.name.endswith(".md"):
                files.append(item.path)
    except Exception:
        pass
    return files


def _get_file_content(repo: Repository, path: str, ref: str) -> str:
    """Get file content as string."""
    try:
        content = repo.get_contents(path, ref=ref)
        if content.content:
            return base64.b64decode(content.content).decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""


def fetch_existing_docs(
    repo: Repository,
    ref: str,
    max_files: int = 50,
    max_content_per_file: int = 8000,
) -> tuple[dict[str, str], str]:
    """
    Fetch existing docs from the target repo.

    Returns:
        (dict of path -> content, nav_structure_text)
    """
    config = get_mkdocs_config(repo, ref)
    docs_dir = get_docs_dir(config)
    nav_text = get_nav_structure(config)

    md_files = _list_md_files(repo, docs_dir, ref)
    md_files = sorted(md_files)[:max_files]

    docs: dict[str, str] = {}
    for path in md_files:
        content = _get_file_content(repo, path, ref)
        if content:
            docs[path] = content[:max_content_per_file] + (
                "\n... (truncated)" if len(content) > max_content_per_file else ""
            )

    return docs, nav_text
