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
    filter_edits_not_preview_only,
    parse_docnerd_response,
    preview_only_paths,
)

logger = logging.getLogger("docnerd.review_loop")

DEFAULT_MAX_WALL_SECONDS = 600
DEFAULT_MAX_ROUNDS = 5

REVIEWER_SYSTEM = """You are an independent documentation reviewer. Your audience is a **public user** of the product who reads only our docs—not the PR or source code.

You receive:
1. The pull request description and code changes (source of truth).
2. **Every loaded documentation page** from the target branch: the writer may have updated some; others are unchanged. Updated pages show more text; unchanged pages show a **short excerpt** so you can still judge fit.

Your job has **two** parts:

### A) Cross-page coverage (required every round)
For **each** file section below (exact path), decide whether that page—given its role in the site—should mention something about this PR, or correctly stay silent.

- **Bad pattern to flag:** One file is overloaded with PR detail while **sibling** pages in the same topic area (e.g. multiple guides under `docs/cli/`, or the same feature split across guides) say **nothing**, even though readers landing there would reasonably expect a pointer or short context.
- **Good pattern:** **One canonical** page carries the full explanation; **related** loaded pages get a **brief** note (1–3 sentences + link to canonical)—not a copy-paste of the whole story.

Include **every** path from the sections below in `file_assessments` (same path strings). For sections marked **PREVIEW ONLY**, use verdict `ok` unless the excerpt clearly proves an error—do **not** use `needs_brief_mention` / `needs_detail` on those paths (the writer cannot save them); instead call out a loaded alternative in `questions` or another file’s `note`.

Use verdict:
- `ok` — appropriate level of coverage for this page’s purpose; no change needed.
- `needs_brief_mention` — page should add a short pointer / context + link; it should not stay silent.
- `needs_detail` — this page is the right home for deeper PR-specific explanation (missing or too thin).
- `trim_or_redistribute` — too much detail **here**; move or shorten here and surface briefly elsewhere as appropriate.
- `incorrect` — contradicts the PR or misleads users.

Each assessment needs a short `note` (one line).

### B) User value
Judge whether the set of docs answers **"Why does the user care about this?"** for user-visible PR changes.

**Mandatory question when revision is needed:** If you return `needs_revision`, your `questions` array **must include** a clear variant of **"Why does the user care about this change?"** tailored to this PR—unless an equivalent is already in the list.

Rules:
- Be strict but fair. Superficial command lists without PR-specific detail are NOT adequate.
- Ask **concrete** questions the writer can answer (not vague "improve this").
- Prefer **small** edits: brief mentions on sibling pages, trim bloat on overloaded pages—avoid demanding huge rewrites unless necessary.

Output **only** a single JSON object in a markdown code fence labeled json. No other text before or after the fence.

Schema:
- If everything is good: `{"status": "satisfied", "file_assessments": [ ... every path ... ]}`
- If not: `{"status": "needs_revision", "questions": ["..."], "file_assessments": [ ... ]}`

`file_assessments` is an array of objects: `{"path": "docs/...", "verdict": "ok|needs_brief_mention|needs_detail|trim_or_redistribute|incorrect", "note": "..."}` — **one entry per file section below**, in any order.

Use at most **8** items in `questions` (synthesize from assessments). Be specific."""


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


_VERDICT_OK = frozenset({"ok", "satisfied", "none", "n/a", "no_change"})


def parse_reviewer_response(text: str) -> tuple[bool, list[str], list[dict[str, str]]]:
    """
    Parse reviewer model output. Returns (satisfied, questions, file_assessments).

    If parsing fails, returns (True, [], []) to avoid infinite loops.
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
        return True, [], []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Reviewer JSON invalid; treating as satisfied: %s", raw[:200])
        return True, [], []

    assessments_raw = data.get("file_assessments", [])
    file_assessments: list[dict[str, str]] = []
    if isinstance(assessments_raw, list):
        for item in assessments_raw:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).strip()
            verdict = str(item.get("verdict", "")).strip()
            note = str(item.get("note", "")).strip()
            if path and verdict:
                file_assessments.append({"path": path, "verdict": verdict, "note": note})

    status = str(data.get("status", "")).lower().replace(" ", "_")
    if status == "satisfied":
        return True, [], file_assessments

    qs = data.get("questions", [])
    if isinstance(qs, str):
        qs = [qs]
    if not isinstance(qs, list):
        qs = []
    questions = [str(q).strip() for q in qs if str(q).strip()]
    return False, questions[:8], file_assessments


def _reviewer_user_prompt(
    pr_text: str,
    draft: dict[str, str],
    baseline: dict[str, str],
    preview_only_paths: set[str],
    *,
    max_chars_updated: int = 12000,
    max_chars_unchanged: int = 3200,
) -> str:
    """
    Show every loaded doc so the reviewer can judge coverage across the tree.
    Writer-updated pages get a larger slice; unchanged pages get a shorter excerpt.
    """
    parts = [
        "## Pull request (what actually changed)",
        pr_text,
        "",
        "## All loaded documentation pages (assess each path in your JSON)",
        "",
        "Sections are sorted by path. **UPDATED** = writer changed this file vs the baseline branch. "
        "**UNCHANGED** = baseline content (excerpt if long); decide if this page should still mention the PR briefly. "
        "**PREVIEW ONLY** = only a truncated excerpt was loaded; the writer **cannot** emit docnerd for that path—use verdict `ok` "
        "or suggest a **fully loaded** sibling/canonical page instead of `needs_brief_mention` on preview paths.",
        "",
    ]
    sorted_paths = sorted(draft.keys())
    for p in sorted_paths:
        content = draft.get(p, "")
        updated = baseline.get(p) != content
        cap = max_chars_updated if updated else max_chars_unchanged
        if p in preview_only_paths:
            label = "PREVIEW ONLY (writer cannot edit this path)"
        elif updated:
            label = "UPDATED by writer"
        else:
            label = "UNCHANGED (excerpt if truncated)"
        if len(content) > cap:
            content = content[:cap] + "\n\n... (truncated for review context)"
        parts.append(f"### {p} ({label})")
        parts.append("```markdown")
        parts.append(content)
        parts.append("```")
        parts.append("")
    parts.append(
        "Respond with only the JSON object in a ```json code block as specified in your instructions. "
        "Include `file_assessments` with **one entry per path** listed above."
    )
    return "\n".join(parts)


def _assessment_flags_revision(assessments: list[dict[str, str]]) -> bool:
    for a in assessments:
        v = str(a.get("verdict", "")).lower().strip().replace(" ", "_")
        if v in _VERDICT_OK:
            continue
        if v in ("needs_brief_mention", "needs_detail", "trim_or_redistribute", "incorrect"):
            return True
    return False


def _questions_from_assessments(
    assessments: list[dict[str, str]], existing: list[str]
) -> list[str]:
    """Turn per-file verdicts into concrete writer tasks (deduped, capped)."""
    out: list[str] = list(existing)
    for a in assessments:
        v = str(a.get("verdict", "")).lower().strip().replace(" ", "_")
        if v in _VERDICT_OK:
            continue
        if v not in ("needs_brief_mention", "needs_detail", "trim_or_redistribute", "incorrect"):
            continue
        path = str(a.get("path", "")).strip()
        note = str(a.get("note", "")).strip()
        if not path:
            continue
        piece = f"`{path}` — {v.replace('_', ' ')}"
        if note:
            piece += f": {note}"
        out.append(piece)
    seen: set[str] = set()
    deduped: list[str] = []
    for q in out:
        if q not in seen:
            seen.add(q)
            deduped.append(q)
    return deduped[:8]


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
    all_doc_paths: list[str] | None = None,
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
    inventory_paths = list(all_doc_paths) if all_doc_paths else sorted(existing_paths)
    preview_blocked = preview_only_paths(existing_docs)
    writer_system = build_system_prompt(
        rules_text,
        target_branch,
        nav_structure,
        list(existing_paths),
        search_terms,
        matching_docs,
        allow_new_files=allow_new_files,
        all_doc_paths_inventory=inventory_paths,
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

        reviewer_user = _reviewer_user_prompt(
            pr_text, draft, existing_docs, preview_blocked
        )
        logger.info(
            "Reviewer round %d: prompt sizes system=%d user=%d changed=%d total_loaded=%d",
            round_idx + 1,
            len(REVIEWER_SYSTEM),
            len(reviewer_user),
            len(review_paths),
            len(draft),
        )

        resp = client.messages.create(
            model=model,
            max_tokens=8192,
            system=REVIEWER_SYSTEM,
            messages=[{"role": "user", "content": reviewer_user}],
        )
        review_text = ""
        for block in resp.content:
            if hasattr(block, "text"):
                review_text += block.text

        satisfied, questions, file_assessments = parse_reviewer_response(review_text)

        if satisfied and _assessment_flags_revision(file_assessments):
            logger.info(
                "Reviewer returned satisfied but file_assessments contain non-ok verdicts; treating as revision"
            )
            satisfied = False
            questions = _questions_from_assessments(file_assessments, questions)

        if satisfied:
            logger.info("Reviewer satisfied after round %d", round_idx + 1)
            break

        if not questions and _assessment_flags_revision(file_assessments):
            questions = _questions_from_assessments(file_assessments, [])

        if not questions:
            logger.info("Reviewer needs_revision but no questions or actionable assessments; stopping")
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
            file_assessments=file_assessments,
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

        refined = filter_edits_not_preview_only(
            parse_docnerd_response(writer_text, existing_paths), preview_blocked
        )
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
