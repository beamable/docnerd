"""DOCNERD_CACHE.yml — per-page summaries for phased routing; auto-maintained by docNerd."""

from __future__ import annotations

import base64
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import yaml
from anthropic import Anthropic
from github.Repository import Repository

from docnerd.doc_generator import DocEdit
from docnerd.llm_context import (
    API_DEFAULT_MAX_RETRIES,
    API_DEFAULT_RETRY_BASE_DELAY_S,
    compute_max_output_tokens,
    messages_create_with_retry,
)

logger = logging.getLogger("docnerd.docnerd_cache")

DEFAULT_CACHE_PATH = "DOCNERD_CACHE.yml"
CACHE_VERSION = 1

DESCRIBE_SYSTEM = """You write short **catalog entries** for documentation pages.
Plain text only (bullets allowed). Target length: roughly 80–200 words unless the page is tiny.

Include:
- Primary audience and what problem the page solves
- Named commands, subcommands, flags, config keys, or APIs the page documents
- Links to related areas (deploy, CLI, Docker, microservices) when relevant

Another model will read **only this summary** (not the full page) together with a PR narrative to decide
whether to run a full edit pass. Be specific enough to tell unrelated PRs from relevant ones; no filler."""

GATE_SYSTEM = """You triage documentation updates. Reply with exactly one word on the first line: PROCEED or SKIP.

SKIP only if the PR narrative clearly does **not** concern this page's topic area.
If there is any plausible connection, or you are unsure, reply PROCEED."""


def load_cache_from_repo(repo: Repository, ref: str, cache_path: str) -> dict[str, Any]:
    empty: dict[str, Any] = {"version": CACHE_VERSION, "files": {}}
    try:
        c = repo.get_contents(cache_path, ref=ref)
        if getattr(c, "type", "") != "file" or not c.content:
            return empty
        raw = base64.b64decode(c.content).decode("utf-8", errors="replace")
        data = yaml.safe_load(raw)
        if not isinstance(data, dict):
            return empty
        data.setdefault("version", CACHE_VERSION)
        files = data.get("files")
        if not isinstance(files, dict):
            data["files"] = {}
        return data
    except Exception:
        return empty


def dump_cache_yaml(data: dict[str, Any]) -> str:
    header = (
        "# DOCNERD_CACHE.yml — auto-maintained by docNerd. Do not edit by hand.\n"
        "# Invalidation: when the markdown file's Git blob SHA changes vs content_sha, the entry is refreshed.\n"
        "# Optional: commit-time check (see doc_generation.phased.docnerd_cache).\n\n"
    )
    body = yaml.dump(
        data,
        default_flow_style=False,
        allow_unicode=True,
        sort_keys=False,
        width=118,
    )
    return header + body


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _latest_commit_iso_for_path(repo: Repository, path: str, ref: str) -> str | None:
    try:
        commits = repo.get_commits(sha=ref, path=path)
        c = commits[0]
        dt = c.commit.committer.date
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return None


def _entry_stale(
    entry: dict[str, Any] | None,
    current_sha: str,
    *,
    check_commit_after_description: bool,
    repo: Repository | None,
    path: str,
    ref: str,
    description_updated_at: str | None,
) -> bool:
    if not entry:
        return True
    if entry.get("content_sha") != current_sha:
        return True
    if (
        check_commit_after_description
        and repo
        and description_updated_at
    ):
        try:
            dt_desc = datetime.fromisoformat(description_updated_at.replace("Z", "+00:00"))
            iso = _latest_commit_iso_for_path(repo, path, ref)
            if iso:
                dt_commit = datetime.fromisoformat(iso.replace("Z", "+00:00"))
                if dt_commit > dt_desc:
                    return True
        except Exception:
            pass
    return False


def claude_describe_page(
    client: Anthropic,
    model: str,
    path: str,
    content_sample: str,
    *,
    max_tokens: int = 2048,
    api_max_retries: int = API_DEFAULT_MAX_RETRIES,
    api_retry_base_delay_s: float = API_DEFAULT_RETRY_BASE_DELAY_S,
) -> str:
    user = f"## File `{path}`\n\n```markdown\n{content_sample}\n```\n"
    mt = compute_max_output_tokens(DESCRIBE_SYSTEM, user, desired_max=max_tokens)
    resp = messages_create_with_retry(
        client,
        max_retries=api_max_retries,
        base_delay_s=api_retry_base_delay_s,
        model=model,
        max_tokens=max(256, mt),
        system=DESCRIBE_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    return "".join(b.text for b in resp.content if hasattr(b, "text")).strip()


def gate_should_run_full_edit(
    client: Anthropic,
    model: str,
    narrative: str,
    path: str,
    page_description: str,
    *,
    max_tokens: int = 256,
    api_max_retries: int = API_DEFAULT_MAX_RETRIES,
    api_retry_base_delay_s: float = API_DEFAULT_RETRY_BASE_DELAY_S,
) -> bool:
    if not page_description.strip():
        return True
    user = (
        "## PR change narrative\n"
        + narrative[:14000]
        + f"\n\n## Page path `{path}`\n## Cached page summary\n{page_description}\n\n"
        "First line of your reply must be exactly PROCEED or SKIP.\n"
    )
    mt = compute_max_output_tokens(GATE_SYSTEM, user, desired_max=max_tokens)
    resp = messages_create_with_retry(
        client,
        max_retries=api_max_retries,
        base_delay_s=api_retry_base_delay_s,
        model=model,
        max_tokens=max(64, mt),
        system=GATE_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in resp.content if hasattr(b, "text")).strip().upper()
    if re.match(r"^SKIP\b", raw):
        return False
    return True


def sync_docnerd_cache(
    repo: Repository,
    ref: str,
    ordered_paths: list[str],
    path_content: dict[str, str],
    path_sha: dict[str, str],
    client: Anthropic,
    model: str,
    *,
    cache_path: str = DEFAULT_CACHE_PATH,
    max_chars_for_describe: int = 48_000,
    describe_max_tokens: int = 2048,
    delay_between_calls_s: float = 0.0,
    check_commit_after_description: bool = False,
    api_max_retries: int = API_DEFAULT_MAX_RETRIES,
    api_retry_base_delay_s: float = API_DEFAULT_RETRY_BASE_DELAY_S,
) -> tuple[dict[str, Any], dict[str, str], bool]:
    """
    Load cache from repo, refresh stale entries via Claude.

    Returns:
        (cache dict for YAML, path -> description for gate, whether YAML should be committed).
    """
    data = load_cache_from_repo(repo, ref, cache_path)
    files_any = data.setdefault("files", {})
    if not isinstance(files_any, dict):
        data["files"] = {}
    files: dict[str, Any] = data["files"]

    descriptions: dict[str, str] = {}
    dirty = False

    for i, path in enumerate(ordered_paths):
        if i > 0 and delay_between_calls_s > 0:
            time.sleep(delay_between_calls_s)

        sha = path_sha.get(path, "")
        content = path_content.get(path, "")
        if not sha or not content:
            continue

        raw_entry = files.get(path)
        entry = raw_entry if isinstance(raw_entry, dict) else None
        desc_at_str = str(entry.get("description_updated_at", "")) if entry else None

        stale = _entry_stale(
            entry,
            sha,
            check_commit_after_description=check_commit_after_description,
            repo=repo,
            path=path,
            ref=ref,
            description_updated_at=desc_at_str,
        )

        if stale:
            sample = (
                content
                if len(content) <= max_chars_for_describe
                else content[:max_chars_for_describe] + "\n... (truncated)\n"
            )
            try:
                desc_text = claude_describe_page(
                    client,
                    model,
                    path,
                    sample,
                    max_tokens=describe_max_tokens,
                    api_max_retries=api_max_retries,
                    api_retry_base_delay_s=api_retry_base_delay_s,
                )
            except Exception:
                logger.exception("Cache describe failed for %s", path)
                if entry and entry.get("description"):
                    descriptions[path] = str(entry["description"])
                    continue
                desc_text = (
                    f"Undocumented page `{path}` (summary generation failed; default to full review)."
                )

            src_iso = _latest_commit_iso_for_path(repo, path, ref) or _now_iso()
            files[path] = {
                "content_sha": sha,
                "description_updated_at": _now_iso(),
                "source_last_modified_at": src_iso,
                "description": desc_text,
            }
            dirty = True
            descriptions[path] = desc_text
        else:
            descriptions[path] = str(entry.get("description", "")) if entry else ""

    data["version"] = CACHE_VERSION
    data["cache_refreshed_at"] = _now_iso()
    return data, descriptions, dirty


def doc_edit_for_cache_file(
    cache_yaml: str,
    cache_path: str,
    *,
    cache_exists_on_branch: bool,
) -> DocEdit:
    return DocEdit(
        path=cache_path,
        content=cache_yaml,
        is_new=not cache_exists_on_branch,
    )


def cache_file_exists_on_branch(repo: Repository, ref: str, cache_path: str) -> bool:
    try:
        c = repo.get_contents(cache_path, ref=ref)
        return getattr(c, "type", "") == "file"
    except Exception:
        return False
