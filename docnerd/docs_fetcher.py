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


def _prioritize_md_paths(
    paths: list[str],
    prioritize_terms: list[str] | None,
    max_files: int,
) -> list[str]:
    """
    Rank paths so PR-relevant docs (e.g. cli/, deploy) are kept when max_files caps the set.

    Plain alphabetical order often drops entire subtrees (e.g. docs/cli/...) when the repo
    has many markdown files earlier in the sort.
    """
    if not paths or max_files <= 0:
        return []
    if not prioritize_terms:
        return sorted(paths)[:max_files]

    tlow = [t.lower() for t in prioritize_terms if len(t) >= 2]
    scored: list[tuple[int, str]] = []
    for p in paths:
        pl = p.lower()
        score = sum(1 for t in tlow if t in pl)
        scored.append((-score, p))
    scored.sort()
    ordered = [p for _, p in scored]
    return ordered[:max_files]


# Substring used to detect preview-tier docs (split prompts; do not docnerd these when present).
DOCS_PREVIEW_ONLY_SENTINEL = "truncated preview — context only"

# Appended to truncated secondary-tier docs so the model does not emit full-file replacements blind.
_PREVIEW_ONLY_TAIL = (
    f"\n\n---\n*[docnerd: {DOCS_PREVIEW_ONLY_SENTINEL}; "
    "do **not** output a docnerd block for this path]*\n"
)


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
    max_files: int | None = None,
    max_priority_files: int | None = None,
    max_content_per_file: int = 6000,
    max_secondary_files: int = 100,
    secondary_content_per_file: int = 2000,
    prioritize_terms: list[str] | None = None,
) -> tuple[dict[str, str], str, list[str]]:
    """
    Fetch existing docs from the target repo.

    - Lists **all** markdown paths under docs_dir (returned as the third tuple element).
    - **Priority** tier: top-ranked paths (PR terms), each up to max_content_per_file chars.
    - **Secondary** tier: next batch of paths with shorter excerpts for **context only**
      (marked so the writer must not emit docnerd for those paths when truncated).

    Args:
        max_files: Deprecated; use max_priority_files. If set, used when max_priority_files is None.
        max_priority_files: How many paths get the primary (longer) content budget.
        prioritize_terms: Substrings from the source PR used to rank paths.

    Returns:
        (path -> content, nav_structure_text, all_markdown_paths_sorted)
    """
    config = get_mkdocs_config(repo, ref)
    docs_dir = get_docs_dir(config)
    nav_text = get_nav_structure(config)

    all_md = sorted(_list_md_files(repo, docs_dir, ref))
    pri_n = max_priority_files if max_priority_files is not None else (max_files if max_files is not None else 50)
    ordered = _prioritize_md_paths(all_md, prioritize_terms, len(all_md))

    docs: dict[str, str] = {}
    for path in ordered[:pri_n]:
        content = _get_file_content(repo, path, ref)
        if not content:
            continue
        cap = max_content_per_file
        if len(content) > cap:
            docs[path] = content[:cap] + "\n... (truncated)"
        else:
            docs[path] = content

    sec_end = pri_n + max(0, max_secondary_files)
    for path in ordered[pri_n:sec_end]:
        if path in docs:
            continue
        content = _get_file_content(repo, path, ref)
        if not content:
            continue
        if len(content) <= secondary_content_per_file:
            docs[path] = content
        else:
            docs[path] = (
                content[:secondary_content_per_file]
                + "\n... (truncated)"
                + _PREVIEW_ONLY_TAIL
            )

    return docs, nav_text, all_md
