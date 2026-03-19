"""Generate and edit documentation using Claude."""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from docnerd.analyzer import PRContext, extract_doc_search_terms, format_pr_context_for_prompt
from docnerd.docs_fetcher import DOCS_PREVIEW_ONLY_SENTINEL
from docnerd.rules_engine import format_rules_for_prompt, load_rules

logger = logging.getLogger("docnerd.doc_generator")


@dataclass
class DocEdit:
    """A single file edit (create or modify)."""

    path: str
    content: str
    is_new: bool


def compute_matching_docs(doc_paths: list[str], search_terms: list[str]) -> list[str]:
    """Compute which docs match the search terms (case-insensitive path contains)."""
    if not search_terms:
        return []
    matches: set[str] = set()
    path_lower = {p: p.lower() for p in doc_paths}
    for term in search_terms:
        t = term.lower()
        for path, pl in path_lower.items():
            if t in pl:
                matches.add(path)
    return sorted(matches)


# PR signals that imply user-facing docs even when terms do not substring-match paths
_USER_FACING_DOC_SIGNALS = frozenset(
    {
        "docker",
        "dockerfile",
        "container",
        "msbuild",
        "csproj",
        "deploy",
        "deployment",
        "release",
        "build",
        "cli",
        "service",
        "services",
        "configuration",
        "config",
        "microservice",
        "image",
        "runtime",
        "property",
        "override",
        "arg",
        "dotnet",
        "alpine",
        "noble",
    }
)

# Doc path substrings for configuration / deploy / CLI / microservice topics
_DOC_PATH_FALLBACK_HINTS = (
    "deploy",
    "config",
    "docker",
    "microservice",
    "build",
    "cli",
    "service",
    "guide",
    "ms-",
    "project",
    "container",
    "command",
    "local",
    "beam",
)


def ensure_matching_docs(
    doc_paths: list[str],
    matching_docs: list[str],
    search_terms: list[str],
    *,
    limit: int = 25,
) -> list[str]:
    """
    If term matching found nothing but the PR clearly affects users (deploy, docker,
    MSBuild, etc.), fall back to docs whose paths look like configuration / CLI / deploy.
    """
    if matching_docs:
        return matching_docs
    st = {t.lower() for t in search_terms}
    if not st & _USER_FACING_DOC_SIGNALS:
        return matching_docs
    hits: list[str] = []
    for p in doc_paths:
        pl = p.lower()
        if any(h in pl for h in _DOC_PATH_FALLBACK_HINTS):
            hits.append(p)
    return sorted(set(hits))[:limit]


def ensure_matching_docs_not_empty_for_user_facing_pr(
    matching_docs: list[str],
    search_terms: list[str],
    existing_paths: set[str],
    *,
    limit: int = 20,
) -> list[str]:
    """
    If the PR is user-facing but no path matched, still target some loaded docs so the
    writer is not told to update an empty list (which invites "no edits").
    """
    if matching_docs:
        return matching_docs
    st = {t.lower() for t in search_terms}
    if not st & _USER_FACING_DOC_SIGNALS:
        return matching_docs
    if not existing_paths:
        return matching_docs
    return sorted(existing_paths)[:limit]


def preview_only_paths(existing_docs: dict[str, str]) -> set[str]:
    """Paths whose loaded body is a truncated preview only (must not receive docnerd output)."""
    return {p for p, c in existing_docs.items() if DOCS_PREVIEW_ONLY_SENTINEL in c}


def filter_edits_not_preview_only(
    edits: list[DocEdit], preview_paths: set[str], *, log_dropped: bool = True
) -> list[DocEdit]:
    """Drop edits targeting preview-only paths (safety net if the model ignores instructions)."""
    if not preview_paths or not edits:
        return edits
    before = len(edits)
    out = [e for e in edits if e.path not in preview_paths]
    dropped = before - len(out)
    if dropped and log_dropped:
        logger.warning(
            "Dropped %d edit(s) targeting preview-only doc path(s) (not fully loaded)",
            dropped,
        )
    return out


def _format_doc_inventory(paths: list[str], max_lines: int = 600) -> str:
    """Full path list for the model to reason about site coverage (capped for huge repos)."""
    if not paths:
        return "(none listed)"
    lines = paths[:max_lines]
    body = "\n".join(f"- {p}" for p in lines)
    if len(paths) > max_lines:
        body += f"\n- ... and {len(paths) - max_lines} more .md files under docs"
    return body


def build_system_prompt(
    rules_text: str,
    target_branch: str,
    nav_structure: str,
    existing_doc_paths: list[str],
    search_terms: list[str],
    matching_docs: list[str],
    allow_new_files: bool = True,
    all_doc_paths_inventory: list[str] | None = None,
    max_inventory_lines: int = 600,
) -> str:
    """Build the system prompt with strict doc generation rules."""
    inventory_paths = (
        list(all_doc_paths_inventory) if all_doc_paths_inventory else sorted(set(existing_doc_paths))
    )
    inventory_text = _format_doc_inventory(inventory_paths, max_inventory_lines)
    terms_str = ", ".join(search_terms) if search_terms else "(none extracted)"
    matching_list = "\n".join(f"- {p}" for p in matching_docs) if matching_docs else "(none)"

    new_files_guidance = ""
    if not allow_new_files:
        new_files_guidance = """
**CRITICAL - new files are disabled:** You may ONLY edit files from the existing list above. Do NOT add links, nav items, or references to files that do not exist. If you add a link in SUMMARY.md or another nav file to a file you would create, that link will be broken because new files are filtered out. Only link to existing docs."""
    elif allow_new_files:
        new_files_guidance = """
**New files:** Only create a new file when no existing doc relates. Be stingy—prefer adding to existing docs. If an existing doc covers deployment, CLI, build, or the same feature area, add there instead."""

    return f"""You are a technical documentation maintainer for an MkDocs site. Your job is to integrate PR changes into EXISTING documentation.

Target branch: {target_branch}

## MkDocs nav structure
```
{nav_structure}
```

## Complete markdown inventory (all .md paths in docs_dir)
Skim this list and consider whether the PR warrants a **small** update anywhere—not only the “matching” paths. Prefer the **fewest** files and **smallest** diffs that still explain the change.

{inventory_text}

**Loaded bodies** (full or excerpt) appear in the user message. You may only output `docnerd` for paths whose body does **not** contain the preview-only notice (truncated excerpt marker).

{new_files_guidance}

## REQUIRED: Strong candidates (pre-computed path matches)

Search terms from PR: {terms_str}

**These paths matched the PR terms** (prioritize here first, but you may choose another **loaded** file if it is the better home):
{matching_list}

**Your task:** Integrate the PR with **surgical** edits. Prefer **1–2** files when the PR is narrow; use **up to ~4–5** when several **sibling** guides in the same topic area (e.g. under `docs/cli/`) each need a **brief** pointer—still avoid pasting the same long section everywhere. Pattern: **one canonical** page with depth; related loaded pages get **1–3 sentences + link**, not duplicate tutorials. Do **not** rewrite whole pages without cause.

**Depth (avoid superficial docs):**
- Ground everything in the PR: use exact command names, flags, and behavior from the diff and file content—never invent options or links.
- Do not write generic command glossaries (e.g. "`beam local run` — starts services") unless you add *specific* detail from the PR: defaults, prerequisites, env vars, typical sequence, or what changed vs before.
- Explain *why* someone runs this in a real workflow, not marketing one-liners.
- For new flags/options: default, effect, and when to tune it (who benefits, tradeoffs).
- Only link to paths that appear in the existing doc file list above. Never add `[text](path.md)` to files that do not exist.

**Voice:** Match the existing page (headings, bullets vs prose). Be dense—**add only what the PR requires**; avoid tutorial bloat and repeated background.

**You MUST output at least one docnerd block** when the PR has **any** user-visible impact, including:
- New or changed CLI flags, options, or commands
- **MSBuild / .csproj properties**, Dockerfile `ARG`s, container base images, or anything that changes how `beam deploy` / `beam build` / Docker builds behave
- Configuration or defaults that affect deployment, local dev, or microservice builds—even if the "API" is an XML property, not a CLI flag

Only output nothing for pure refactors, comments-only, or generated code with zero behavioral change.

## Output format (machine-parsed — follow exactly)
For **each** file you edit, output one block. The opening line must match this pattern (case-insensitive): three backticks, `docnerd`, colon, then the **exact** repo-relative path, then a newline. Then the **entire** markdown file. Then optionally a closing line of three backticks.

**Important:** Your markdown will often contain other ``` code fences. That is fine. The **next** edited file must start with a **new** line ` ```docnerd:other/path.md` — the parser uses that to find where the previous file ends. Do not rely on a single closing ``` before the next file.

Example (two files):
```docnerd:docs/cli/foo.md
# Title
```csharp
example();
```
More prose.
```docnerd:docs/cli/bar.md
# Second file
...
```

- Use EXACT paths from the existing doc list
- Output the COMPLETE file content (preserve unchanged parts)
- Only output nothing if the PR has ZERO user-facing changes (pure refactors, comments-only, generated-only)

{rules_text}
"""


def build_user_prompt(
    pr_context_text: str,
    existing_docs: dict[str, str],
    search_terms: list[str],
    matching_docs: list[str],
) -> str:
    """Build the user prompt with PR context and existing docs."""
    matching_preview = ", ".join(matching_docs[:8]) if matching_docs else "see system prompt"
    priority_docs = {k: v for k, v in existing_docs.items() if DOCS_PREVIEW_ONLY_SENTINEL not in v}
    preview_docs = {k: v for k, v in existing_docs.items() if DOCS_PREVIEW_ONLY_SENTINEL in v}

    parts = [
        "## Task",
        "",
        f"Strong path matches (search terms): {matching_preview}",
        "",
        "Document what changed with **minimal** edits: prefer a short subsection, table row, or paragraph—not a full page rewrite. "
        "Usually **1–2** `docnerd` files; use more only when **multiple sibling guides** in the same topic folder should each get a **short** mention + link to the canonical page (do not duplicate long explanations). "
        "Skim **preview** sections only to decide where a loaded (non-preview) page should carry the change.",
        "",
        "---",
        "",
        pr_context_text,
        "",
        "---",
        "",
        "## Loaded documentation — safe to edit (full or long slice; emit docnerd for these only)",
        "",
    ]

    shown_pri: set[str] = set()
    for path in matching_docs:
        if path in priority_docs:
            parts.append(f"### {path} (MATCH — prefer updating here if it fits)")
            parts.append("```markdown")
            parts.append(priority_docs[path])
            parts.append("```")
            parts.append("")
            shown_pri.add(path)

    for path in sorted(priority_docs.keys()):
        if path in shown_pri:
            continue
        parts.append(f"### {path}")
        parts.append("```markdown")
        parts.append(priority_docs[path])
        parts.append("```")
        parts.append("")

    if preview_docs:
        parts.append("## Preview excerpts — context only (do **not** emit docnerd for these paths)")
        parts.append("")
        for path in sorted(preview_docs.keys()):
            parts.append(f"### {path}")
            parts.append("```markdown")
            parts.append(preview_docs[path])
            parts.append("```")
            parts.append("")

    parts.append(
        "Output docnerd blocks only for paths in the **safe to edit** section. Use exact paths. Full file content per block. "
        "You MUST update at least one **safe** doc when the PR has user-visible impact. "
        "If you touch SUMMARY.md or nav, every link target must exist in the inventory."
        "\n\n**Fence rule:** Each file starts with ```docnerd:PATH then newline; use a new ```docnerd: line for the next file."
    )

    return "\n".join(parts)


def build_refine_user_prompt(
    pr_context_text: str,
    draft_docs: dict[str, str],
    reviewer_questions: list[str],
    search_terms: list[str],
    matching_docs: list[str],
    touched_paths: set[str],
    file_assessments: list[dict[str, str]] | None = None,
) -> str:
    """User prompt for a refinement pass after reviewer feedback."""
    matching_preview = ", ".join(matching_docs[:8]) if matching_docs else "see system prompt"
    q_lines = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(reviewer_questions))

    paths_to_show: set[str] = set(touched_paths)
    paths_to_show.update(matching_docs)
    if file_assessments:
        for a in file_assessments:
            v = str(a.get("verdict", "")).lower().strip().replace(" ", "_")
            if v in ("ok", "satisfied", "none", "n/a", "no_change"):
                continue
            p = str(a.get("path", "")).strip()
            if p:
                paths_to_show.add(p)

    parts = [
        "## Reviewer feedback — address every point in the documentation",
        "",
        q_lines,
        "",
    ]
    if file_assessments:
        parts.extend(
            [
                "## Reviewer per-file coverage (implement verdicts; use brief text on sibling pages)",
                "",
            ]
        )
        for a in sorted(file_assessments, key=lambda x: str(x.get("path", ""))):
            path = str(a.get("path", "")).strip()
            verdict = str(a.get("verdict", "")).strip()
            note = str(a.get("note", "")).strip()
            if not path:
                continue
            parts.append(f"- **`{path}`** — `{verdict}`" + (f": {note}" if note else ""))
        parts.append("")

    parts.extend(
        [
            "Revise with **small** diffs. Output full file content in `docnerd` blocks for **each** file you change. "
            "If the table above flags several guides, you may touch **multiple** paths—use **short** additions + links on secondary pages; keep depth on the **canonical** page. "
            "For `trim_or_redistribute`, shorten the overloaded file and add brief pointers elsewhere as indicated.",
            "",
            "## Task",
            "",
            f"Matching docs (search terms): {matching_preview}",
            "",
            "---",
            "",
            pr_context_text,
            "",
            "---",
            "",
            "## Current documentation draft (revise as needed; use EXACT paths)",
            "",
        ]
    )

    ordered = sorted(paths_to_show, key=lambda p: (0 if p in matching_docs else 1, p))
    for path in ordered:
        if path not in draft_docs:
            continue
        label = " (MATCH)" if path in matching_docs else ""
        parts.append(f"### {path}{label}")
        parts.append("```markdown")
        parts.append(draft_docs[path])
        parts.append("```")
        parts.append("")

    parts.append(
        "Output docnerd blocks with complete file content for every file you change. "
        "Match the reviewer’s spread: brief on secondary guides, depth on the canonical page. "
        "If you touch SUMMARY.md or nav, every link target must exist in the repo doc set."
    )

    return "\n".join(parts)


_DOCNERD_OPEN = re.compile(r"(?i)```\s*docnerd:\s*([^\n\r]+?)\s*\r?\n")


def parse_docnerd_response(
    response_text: str,
    existing_paths: set[str],
) -> list[DocEdit]:
    """
    Parse Claude's response to extract file edits.
    Marks as is_new=False for paths that existed.

    Uses **next-block** boundaries: each edit runs from `` ```docnerd:path`` through the
    character before the next `` ```docnerd:`` (or EOF). This avoids the classic bug where
    ``(.*?)``` `` stops at the first ``` inside markdown content (code fences).
    """
    edits: list[DocEdit] = []
    seen_paths: set[str] = set()
    matches = list(_DOCNERD_OPEN.finditer(response_text))
    for i, m in enumerate(matches):
        path = m.group(1).strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(response_text)
        chunk = response_text[start:end]
        chunk = re.sub(r"\r?\n```\s*$", "", chunk.rstrip())
        chunk = chunk.strip()
        if path and chunk and path not in seen_paths:
            seen_paths.add(path)
            is_new = path not in existing_paths
            edits.append(DocEdit(path=path, content=chunk, is_new=is_new))

    if not edits:
        edits = _parse_docnerd_json_payloads(response_text, existing_paths)

    return edits


def _extract_json_fence_bodies(text: str) -> list[str]:
    """Return raw JSON strings inside ```json ... ``` fences (handles braces inside strings)."""
    bodies: list[str] = []
    pos = 0
    while pos < len(text):
        m = re.search(r"```(?:json)?\s*\n", text[pos:], re.IGNORECASE)
        if not m:
            break
        start = pos + m.end()
        m2 = re.search(r"\n```\s*(?:\n|$)", text[start:])
        if not m2:
            break
        bodies.append(text[start : start + m2.start()])
        pos = start + m2.end()
    return bodies


def _parse_docnerd_json_payloads(response_text: str, existing_paths: set[str]) -> list[DocEdit]:
    """Fallback: JSON object with array of {path, content} inside a ```json fence."""
    edits: list[DocEdit] = []
    seen_paths: set[str] = set()
    for raw in _extract_json_fence_bodies(response_text):
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        items: list[dict] | None = None
        for key in ("docnerd_files", "docnerd_edits", "updates", "files"):
            val = data.get(key) if isinstance(data, dict) else None
            if isinstance(val, list):
                items = val
                break
        if not items:
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            path = str(item.get("path", "")).strip()
            content = item.get("content")
            if not path or not isinstance(content, str) or not content.strip():
                continue
            if path in seen_paths:
                continue
            seen_paths.add(path)
            edits.append(
                DocEdit(path=path, content=content.strip(), is_new=path not in existing_paths)
            )
    return edits


class DocGenerator:
    """Generate documentation using Claude."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        base_url: str | None = None,
        rules_path: str | Path = "rules",
    ):
        self.client = Anthropic(api_key=api_key, base_url=base_url or None)
        self.model = model
        self.rules = load_rules(rules_path)
        self.rules_text = format_rules_for_prompt(self.rules)

    def generate(
        self,
        pr_context: PRContext,
        target_branch: str,
        existing_docs: dict[str, str],
        nav_structure: str = "(no nav)",
        allow_new_files: bool = True,
        review_loop: dict | None = None,
        all_doc_paths: list[str] | None = None,
    ) -> list[DocEdit]:
        """
        Generate documentation edits based on PR context.

        Args:
            pr_context: Analyzed PR context
            target_branch: Target docs branch (e.g. core/v7.1)
            existing_docs: Dict of path -> content for existing docs (required)
            nav_structure: MkDocs nav structure text
            all_doc_paths: All .md paths under docs_dir (for inventory); defaults to loaded keys

        Returns:
            List of DocEdit (path, content, is_new)
        """
        existing_paths = set(existing_docs.keys())
        search_terms = extract_doc_search_terms(pr_context)
        matching_docs = ensure_matching_docs(
            list(existing_paths),
            compute_matching_docs(list(existing_paths), search_terms),
            search_terms,
        )
        matching_docs = ensure_matching_docs_not_empty_for_user_facing_pr(
            matching_docs, search_terms, existing_paths
        )
        if matching_docs:
            logger.info("Matching docs for writer: %s", matching_docs[:12])

        inventory_paths = list(all_doc_paths) if all_doc_paths else sorted(existing_paths)
        preview_blocked = preview_only_paths(existing_docs)

        system_prompt = build_system_prompt(
            self.rules_text,
            target_branch,
            nav_structure,
            list(existing_paths),
            search_terms,
            matching_docs,
            allow_new_files=allow_new_files,
            all_doc_paths_inventory=inventory_paths,
        )
        pr_text = format_pr_context_for_prompt(pr_context)
        user_prompt = build_user_prompt(pr_text, existing_docs, search_terms, matching_docs)

        logger.info("=== Prompts (summary - source code omitted from logs) ===")
        logger.info("System prompt: %d chars", len(system_prompt))
        logger.info("User prompt: %d chars (PR context + existing docs - not logged)", len(user_prompt))

        response = self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )

        text = ""
        for block in response.content:
            if hasattr(block, "text"):
                text += block.text

        edits_result = filter_edits_not_preview_only(
            parse_docnerd_response(text, existing_paths), preview_blocked
        )
        if edits_result:
            paths = [e.path for e in edits_result]
            logger.info("Claude response: %d edit(s) to %s", len(edits_result), paths)
        else:
            logger.info("Claude response: no docnerd blocks parsed (raw length %d chars)", len(text))
            if re.search(r"(?i)docnerd", text):
                logger.warning(
                    "Response mentions docnerd but parser found no edits; snippet: %r",
                    text[:400].replace("\n", "\\n"),
                )

        review_cfg = review_loop if review_loop is not None else {}
        if review_cfg.get("enabled", True) and edits_result:
            from docnerd.review_loop import run_review_refinement_loop

            max_wall = float(review_cfg.get("max_wall_seconds", 600))
            max_rounds = int(review_cfg.get("max_rounds", 5))
            logger.info(
                "Starting review/refinement loop (max %.0fs wall, max %d rounds)",
                max_wall,
                max_rounds,
            )
            edits_result = run_review_refinement_loop(
                self.client,
                self.model,
                pr_context,
                target_branch,
                nav_structure,
                existing_docs,
                existing_paths,
                search_terms,
                matching_docs,
                self.rules_text,
                allow_new_files,
                edits_result,
                all_doc_paths=inventory_paths,
                max_wall_seconds=max_wall,
                max_rounds=max_rounds,
            )
            if edits_result:
                logger.info(
                    "After review loop: %d final edit(s) to %s",
                    len(edits_result),
                    [e.path for e in edits_result],
                )

        return edits_result
