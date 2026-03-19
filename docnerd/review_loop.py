"""Reviewer + writer refinement loop for documentation generation."""

from __future__ import annotations

import json
import logging
import re
import time
from anthropic import Anthropic

from docnerd.analyzer import PRContext, format_pr_context_for_prompt
from docnerd.doc_generator import (
    DocEdit,
    build_refine_user_prompt,
    build_system_prompt,
    parse_docnerd_response,
)

logger = logging.getLogger("docnerd.review_loop")

DEFAULT_MAX_WALL_SECONDS = 600
DEFAULT_MAX_ROUNDS = 8

REVIEWER_SYSTEM = """You are an independent documentation reviewer. Your audience is a **public user** of the product who reads only our docs—not the PR or source code.

You receive:
1. The pull request description and code changes (source of truth).
2. The **proposed** documentation draft (markdown) that another writer produced.

Your job: decide whether the docs adequately explain **what changed** and **why it matters** to that user (behavior, options, defaults, workflows, caveats).

**Mandatory lens (every review):** You must explicitly judge whether the draft answers **"Why does the user care about this?"** for the PR's user-visible changes. If that is missing, buried, or only obvious to someone who read the code, respond with `needs_revision`.

**Mandatory question when revision is needed:** If you return `needs_revision`, your `questions` array **must include** a clear variant of **"Why does the user care about this change?"** tailored to this PR (e.g. tie it to deploy failures, configuration, or workflow impact)—unless you already asked an equivalent specific question in the same list. Do not omit this user-value angle.

Rules:
- Be strict but fair. Superficial command lists without PR-specific detail are NOT adequate.
- If anything important for a user is missing, unclear, or wrong relative to the PR, require revision.
- Ask **concrete** questions the writer can answer by updating the docs (not vague "improve this").

Output **only** a single JSON object in a markdown code fence labeled json. No other text before or after the fence.

Schema:
- If the docs are good enough: `{"status": "satisfied"}`
- If not: `{"status": "needs_revision", "questions": ["question 1", "question 2", ...]}`

Use at most 8 questions per round. Be specific."""


def apply_edits_to_draft(base: dict[str, str], edits: list[DocEdit]) -> dict[str, str]:
    """Merge doc edits into a draft copy of docs."""
    out = dict(base)
    for e in edits:
        out[e.path] = e.content
    return out


def draft_to_final_edits(original: dict[str, str], draft: dict[str, str]) -> list[DocEdit]:
    """Produce DocEdit list for every path whose content differs from original."""
    existing_paths = set(original.keys())
    result: list[DocEdit] = []
    for path, content in sorted(draft.items()):
        if path not in original or original[path] != content:
            result.append(DocEdit(path=path, content=content, is_new=path not in existing_paths))
    return result


def parse_reviewer_response(text: str) -> tuple[bool, list[str]]:
    """
    Parse reviewer model output. Returns (satisfied, questions).

    If parsing fails, returns (True, []) to avoid infinite loops.
    """
    raw: str | None = None
    m = re.search(r"```(?:json)?\s*([\s\S]*?)```", text.strip())
    if m:
        raw = m.group(1).strip()
    else:
        m2 = re.search(r"\{[\s\S]*\"status\"[\s\S]*\}", text)
        if m2:
            raw = m2.group(0).strip()
    if not raw:
        logger.warning("Reviewer response had no JSON; treating as satisfied")
        return True, []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Reviewer JSON invalid; treating as satisfied: %s", raw[:200])
        return True, []

    status = str(data.get("status", "")).lower().replace(" ", "_")
    if status == "satisfied":
        return True, []

    qs = data.get("questions", [])
    if isinstance(qs, str):
        qs = [qs]
    if not isinstance(qs, list):
        qs = []
    questions = [str(q).strip() for q in qs if str(q).strip()]
    return False, questions[:8]


def _reviewer_user_prompt(pr_text: str, draft: dict[str, str], paths: list[str], max_chars_per_file: int = 16000) -> str:
    parts = [
        "## Pull request (what actually changed)",
        pr_text,
        "",
        "## Proposed documentation (your review target)",
        "",
    ]
    for p in paths:
        content = draft.get(p, "")
        if len(content) > max_chars_per_file:
            content = content[:max_chars_per_file] + "\n\n... (truncated for review context)"
        parts.append(f"### {p}")
        parts.append("```markdown")
        parts.append(content)
        parts.append("```")
        parts.append("")
    parts.append(
        "Respond with only the JSON object in a ```json code block as specified in your instructions."
    )
    return "\n".join(parts)


def run_review_refinement_loop(
    client: Anthropic,
    model: str,
    pr_context: PRContext,
    target_branch: str,
    nav_structure: str,
    existing_docs: dict[str, str],
    existing_paths: set[str],
    search_terms: list[str],
    matching_docs: list[str],
    rules_text: str,
    allow_new_files: bool,
    initial_edits: list[DocEdit],
    *,
    max_wall_seconds: float = DEFAULT_MAX_WALL_SECONDS,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
) -> list[DocEdit]:
    """
    Run reviewer → refine loop until satisfied, max rounds, or wall clock exceeded.

    Returns final DocEdit list relative to original existing_docs.
    """
    if not initial_edits:
        return initial_edits

    deadline = time.monotonic() + max_wall_seconds
    pr_text = format_pr_context_for_prompt(pr_context)
    writer_system = build_system_prompt(
        rules_text,
        target_branch,
        nav_structure,
        list(existing_paths),
        search_terms,
        matching_docs,
        allow_new_files=allow_new_files,
    )

    draft = apply_edits_to_draft(existing_docs, initial_edits)
    touched_paths: set[str] = {e.path for e in initial_edits}

    round_idx = 0
    while round_idx < max_rounds:
        if time.monotonic() >= deadline:
            logger.info("Review loop stopped: wall clock limit (%.0fs)", max_wall_seconds)
            break

        # Paths that differ from original baseline (what we ship)
        review_paths = sorted(
            p for p, c in draft.items() if p not in existing_docs or existing_docs[p] != c
        )
        if not review_paths:
            break

        reviewer_user = _reviewer_user_prompt(pr_text, draft, review_paths)
        logger.info(
            "Reviewer round %d: prompt sizes system=%d user=%d paths=%s",
            round_idx + 1,
            len(REVIEWER_SYSTEM),
            len(reviewer_user),
            review_paths,
        )

        resp = client.messages.create(
            model=model,
            max_tokens=4096,
            system=REVIEWER_SYSTEM,
            messages=[{"role": "user", "content": reviewer_user}],
        )
        review_text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                review_text += block.text

        satisfied, questions = parse_reviewer_response(review_text)
        if satisfied:
            logger.info("Reviewer satisfied after round %d", round_idx + 1)
            break

        if not questions:
            logger.info("Reviewer needs_revision but no questions; stopping")
            break

        if time.monotonic() >= deadline:
            logger.info("Review loop stopped before refine: wall clock limit")
            break

        refine_user = build_refine_user_prompt(
            pr_text,
            draft,
            questions,
            search_terms,
            matching_docs,
            touched_paths,
        )
        logger.info(
            "Refinement round %d: addressing %d question(s), user prompt %d chars",
            round_idx + 1,
            len(questions),
            len(refine_user),
        )

        resp_w = client.messages.create(
            model=model,
            max_tokens=16000,
            system=writer_system,
            messages=[{"role": "user", "content": refine_user}],
        )
        writer_text = ""
        for block in resp_w.content:
            if hasattr(block, "text"):
                writer_text += block.text

        refined = parse_docnerd_response(writer_text, existing_paths)
        if not refined:
            logger.warning("Refinement produced no docnerd blocks; keeping previous draft")
            round_idx += 1
            continue

        draft = apply_edits_to_draft(draft, refined)
        for e in refined:
            touched_paths.add(e.path)
        current_edits = draft_to_final_edits(existing_docs, draft)
        round_idx += 1

    return draft_to_final_edits(existing_docs, draft)
