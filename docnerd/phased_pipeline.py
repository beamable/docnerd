"""Phased Claude flow: PR narrative file -> per-doc calls -> adequacy -> optional expansion."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import re
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from docnerd.analyzer import PRContext, format_pr_context_for_prompt
from docnerd.doc_generator import DocEdit, parse_docnerd_response, preview_only_paths
from docnerd.llm_context import compute_max_output_tokens

logger = logging.getLogger("docnerd.phased_pipeline")

ADEQUACY_SYSTEM = """You judge whether **proposed documentation edits** (summarized below) adequately cover a **PR change narrative** for public readers.

Output **only** JSON inside a ```json code fence:
{"adequate": true}
or
{"adequate": false, "gap": "what is missing for users", "actions": ["optional concrete hints, e.g. mention X in docs/cli/foo.md"]}

If important user-facing behavior from the narrative is missing from the edits, adequate must be false."""


def dedupe_edits(edits: list[DocEdit]) -> list[DocEdit]:
    by_path: dict[str, DocEdit] = {}
    for e in edits:
        by_path[e.path] = e
    return list(by_path.values())


def write_pr_narrative_markdown(
    client: Anthropic,
    model: str,
    pr_context: PRContext,
    rules_text: str,
    *,
    max_tokens: int = 8192,
    workdir: Path | None = None,
) -> str:
    """Claude writes a standalone PR change document (local handoff artifact)."""
    pr_text = format_pr_context_for_prompt(pr_context)
    system = (
        "You write an internal **PR change document** in Markdown for technical writers.\n"
        "Other automated steps will update **one documentation file at a time** using only this narrative "
        "(not the raw PR). Be precise about user-visible behavior: CLI flags, config, defaults, workflows, "
        "breaking changes, and deployment/build impact.\n"
        "Do not reference specific docs repo paths unless they appear in the PR itself. "
        "Do not use ```docnerd blocks.\n\n"
        f"Project rules (excerpt):\n{rules_text[:8000]}"
    )
    user = "## Pull request\n\n" + pr_text
    mt = compute_max_output_tokens(system, user, desired_max=max_tokens)
    resp = client.messages.create(
        model=model,
        max_tokens=max(1024, mt),
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in resp.content if hasattr(b, "text")).strip()
    wd = workdir or Path.cwd()
    out_dir = wd / ".docnerd"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pr_change_narrative.md").write_text(text, encoding="utf-8")
    logger.info("Wrote PR narrative (%d chars) to %s", len(text), out_dir / "pr_change_narrative.md")
    return text


def _parse_per_doc_response(
    response_text: str, expected_path: str, existing_paths: set[str]
) -> DocEdit | None:
    t = response_text.strip()
    if re.match(r"(?is)^\s*NO_EDIT\s*$", t):
        return None
    edits = parse_docnerd_response(t, existing_paths)
    for e in edits:
        if e.path == expected_path:
            return e
    if len(edits) == 1:
        return edits[0]
    return None


def suggest_one_doc(
    client: Anthropic,
    model: str,
    doc_path: str,
    doc_content: str,
    narrative: str,
    rules_excerpt: str,
    existing_paths: set[str],
    allow_new_files: bool,
    *,
    max_tokens: int = 16_384,
) -> DocEdit | None:
    system = (
        f"You maintain **one** documentation file: `{doc_path}`.\n"
        "You receive a PR change narrative and the file's current markdown.\n\n"
        "- If this page should **not** change, reply with exactly: NO_EDIT\n"
        "- Otherwise output exactly one block starting with a line:\n"
        f"```docnerd:{doc_path}\n"
        "then the **complete** new markdown file. Other ``` fences in the file are allowed.\n"
    )
    if not allow_new_files:
        system += f"\nYou may only edit `{doc_path}`.\n"
    system += f"\nRules excerpt:\n{rules_excerpt[:4000]}\n"
    user = (
        "## PR change narrative\n\n"
        + narrative
        + f"\n\n## Current file `{doc_path}`\n\n```markdown\n"
        + doc_content
        + "\n```\n"
    )
    mt = compute_max_output_tokens(system, user, desired_max=max_tokens)
    resp = client.messages.create(
        model=model,
        max_tokens=max(1024, mt),
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
    return _parse_per_doc_response(raw, doc_path, existing_paths)


def run_per_doc_pass_parallel(
    client: Anthropic,
    model: str,
    full_docs: dict[str, str],
    ordered_paths: list[str],
    narrative: str,
    rules_text: str,
    allow_new_files: bool,
    *,
    max_workers: int = 4,
) -> list[DocEdit]:
    existing_paths = set(full_docs.keys())
    excerpt = rules_text[:8000]

    def job(path: str) -> DocEdit | None:
        content = full_docs.get(path, "")
        if not content:
            return None
        try:
            return suggest_one_doc(
                client,
                model,
                path,
                content,
                narrative,
                excerpt,
                existing_paths,
                allow_new_files,
            )
        except Exception:
            logger.exception("Per-doc Claude call failed for %s", path)
            return None

    if max_workers <= 1:
        merged = [job(p) for p in ordered_paths]
    else:
        path_result: dict[str, DocEdit | None] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = {pool.submit(job, p): p for p in ordered_paths}
            for fut in concurrent.futures.as_completed(futs):
                p = futs[fut]
                try:
                    path_result[p] = fut.result()
                except Exception:
                    logger.exception("Per-doc future failed for %s", p)
                    path_result[p] = None
        merged = [path_result[p] for p in ordered_paths if p in path_result]

    out: list[DocEdit] = []
    for e in merged:
        if e is not None:
            out.append(e)
    return dedupe_edits(out)


def evaluate_adequacy(
    client: Anthropic,
    model: str,
    narrative: str,
    edits: list[DocEdit],
    *,
    max_tokens: int = 4096,
) -> tuple[bool, dict[str, Any]]:
    if not edits:
        return False, {"gap": "No edits proposed", "actions": []}
    summaries = []
    for e in edits:
        snippet = e.content[:240].replace("\n", " ")
        summaries.append(f"- `{e.path}` ({len(e.content)} chars): {snippet}...")
    user = (
        "## PR change narrative\n\n"
        + narrative
        + "\n\n## Proposed edits (summary)\n\n"
        + "\n".join(summaries)
    )
    mt = compute_max_output_tokens(ADEQUACY_SYSTEM, user, desired_max=max_tokens)
    resp = client.messages.create(
        model=model,
        max_tokens=max(1024, mt),
        system=ADEQUACY_SYSTEM,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw)
    if not m:
        return True, {}
    try:
        data = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return True, {}
    ok = bool(data.get("adequate", True))
    return ok, data if isinstance(data, dict) else {}


def run_expansion_call(
    client: Anthropic,
    model: str,
    narrative: str,
    gap_payload: dict[str, Any],
    nav_structure: str,
    all_paths: list[str],
    rules_text: str,
    allow_new_files: bool,
    existing_paths: set[str],
    *,
    max_tokens: int = 24_000,
) -> list[DocEdit]:
    preview = ", ".join(all_paths[:50])
    if len(all_paths) > 50:
        preview += ", ..."
    system = (
        "You add or extend documentation to close gaps identified after a first pass.\n"
        "Output one or more ```docnerd:path\\nfull file...``` blocks.\n"
        f"Some known paths: {preview}\n\n"
        f"MkDocs nav (excerpt):\n```\n{nav_structure[:8000]}\n```\n\n"
        f"Rules (excerpt):\n{rules_text[:6000]}\n"
    )
    if not allow_new_files:
        system += "\n**New files are disabled** — only edit existing paths from the repo.\n"
    user = (
        "## PR change narrative\n"
        + narrative
        + "\n\n## Gap analysis (from adequacy check)\n```json\n"
        + json.dumps(gap_payload, indent=2)[:12000]
        + "\n```\n"
    )
    mt = compute_max_output_tokens(system, user, desired_max=max_tokens)
    resp = client.messages.create(
        model=model,
        max_tokens=max(1024, mt),
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    raw = "".join(b.text for b in resp.content if hasattr(b, "text"))
    return parse_docnerd_response(raw, existing_paths)


def run_phased_generation(
    client: Anthropic,
    model: str,
    rules_text: str,
    pr_context: PRContext,
    target_branch: str,
    nav_structure: str,
    full_docs: dict[str, str],
    all_doc_paths: list[str],
    allow_new_files: bool,
    review_loop_cfg: dict[str, Any] | None,
    phased_cfg: dict[str, Any] | None,
) -> list[DocEdit]:
    """
    Full phased pipeline + edit-based review loop (when enabled).
    """
    phased_cfg = phased_cfg or {}
    review_loop_cfg = review_loop_cfg or {}

    paths = sorted(all_doc_paths) if all_doc_paths else sorted(full_docs.keys())
    if not paths:
        logger.warning("Phased generation: no doc paths; returning no edits")
        return []

    narrative = write_pr_narrative_markdown(
        client,
        model,
        pr_context,
        rules_text,
        max_tokens=int(phased_cfg.get("narrative_max_tokens", 8192)),
    )

    parallel = max(1, int(phased_cfg.get("max_parallel_doc_calls", 4)))
    edits = run_per_doc_pass_parallel(
        client,
        model,
        full_docs,
        paths,
        narrative,
        rules_text,
        allow_new_files,
        max_workers=parallel,
    )

    preview_blocked = preview_only_paths(full_docs)
    edits = [e for e in edits if e.path not in preview_blocked]
    edits = dedupe_edits(edits)
    logger.info("Per-doc pass complete: %d edit(s) after preview filter", len(edits))

    adequate, gap_data = evaluate_adequacy(
        client,
        model,
        narrative,
        edits,
        max_tokens=int(phased_cfg.get("adequacy_max_tokens", 4096)),
    )
    if not adequate:
        logger.info("Adequacy check not satisfied; running expansion pass")
        more = run_expansion_call(
            client,
            model,
            narrative,
            gap_data,
            nav_structure,
            paths,
            rules_text,
            allow_new_files,
            set(full_docs.keys()),
            max_tokens=int(phased_cfg.get("expansion_max_tokens", 24_000)),
        )
        more = [e for e in more if e.path not in preview_blocked]
        edits = dedupe_edits(edits + more)
        logger.info("After expansion: %d edit(s)", len(edits))

    if review_loop_cfg.get("enabled", True) and edits:
        from docnerd.review_loop import run_edit_based_review_loop

        edits = run_edit_based_review_loop(
            client,
            model,
            narrative,
            pr_context,
            target_branch,
            nav_structure,
            dict(full_docs),
            set(full_docs.keys()),
            rules_text,
            allow_new_files,
            edits,
            all_doc_paths=paths,
            max_wall_seconds=float(review_loop_cfg.get("max_wall_seconds", 600)),
            max_rounds=int(review_loop_cfg.get("max_rounds", 5)),
        )

    logger.info("Phased generation finished: %d final edit(s) for branch %s", len(edits), target_branch)
    return edits
