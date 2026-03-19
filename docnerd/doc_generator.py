"""Generate and edit documentation using Claude."""

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from docnerd.analyzer import PRContext, extract_doc_search_terms, format_pr_context_for_prompt
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


def build_system_prompt(
    rules_text: str,
    target_branch: str,
    nav_structure: str,
    existing_doc_paths: list[str],
    search_terms: list[str],
    matching_docs: list[str],
    allow_new_files: bool = True,
) -> str:
    """Build the system prompt with strict doc generation rules."""
    paths_list = "\n".join(f"- {p}" for p in existing_doc_paths[:80])
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

## Existing doc files (use EXACT paths from this list)
{paths_list}
{new_files_guidance}

## REQUIRED: Docs you MUST update (pre-computed matches)

Search terms from PR: {terms_str}

**These docs match and MUST be updated** (path contains deploy, plan, build, cli, command, etc.):
{matching_list}

**Your task:** Integrate the PR into the docs above. Prefer docs/cli/, docs/cli/commands/, or deployment guides. Update the pages that best explain the *changed* behavior.

**Depth (avoid superficial docs):**
- Ground everything in the PR: use exact command names, flags, and behavior from the diff and file content—never invent options or links.
- Do not write generic command glossaries (e.g. "`beam local run` — starts services") unless you add *specific* detail from the PR: defaults, prerequisites, env vars, typical sequence, or what changed vs before.
- Explain *why* someone runs this in a real workflow, not marketing one-liners.
- For new flags/options: default, effect, and when to tune it (who benefits, tradeoffs).
- Only link to paths that appear in the existing doc file list above. Never add `[text](path.md)` to files that do not exist.

**Voice:** Match the existing page (headings, bullets vs prose). Be dense and useful—no filler—but **substance beats brevity** when the PR warrants a short paragraph or a worked example.

**You MUST output at least one docnerd block** when the PR has **any** user-visible impact, including:
- New or changed CLI flags, options, or commands
- **MSBuild / .csproj properties**, Dockerfile `ARG`s, container base images, or anything that changes how `beam deploy` / `beam build` / Docker builds behave
- Configuration or defaults that affect deployment, local dev, or microservice builds—even if the "API" is an XML property, not a CLI flag

Only output nothing for pure refactors, comments-only, or generated code with zero behavioral change.

## Output format
For each file you edit, output:
```docnerd:path/to/existing/file.md
<full file content with your edits>
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
    parts = [
        "## Task",
        "",
        f"Matching docs to update (from search terms): {matching_preview}",
        "",
        "Integrate the PR deeply: document what actually changed, with enough detail that a reader could use it without reading the code. Match each page's voice. Prefer concrete workflow context over shallow command lists.",
        "",
        "---",
        "",
        pr_context_text,
        "",
        "---",
        "",
        "## Existing documentation (use EXACT paths - update at least one matching doc)",
        "",
    ]

    # Put matching docs first so they're prominent
    for path in matching_docs:
        if path in existing_docs:
            parts.append(f"### {path} (MATCH - update this)")
            parts.append("```markdown")
            parts.append(existing_docs[path])
            parts.append("```")
            parts.append("")

    for path, content in existing_docs.items():
        if path not in matching_docs:
            parts.append(f"### {path}")
            parts.append("```markdown")
            parts.append(content)
            parts.append("```")
            parts.append("")

    parts.append(
        "Output docnerd blocks. Use exact paths. Include full file content. "
        "You MUST update at least one doc when the PR adds CLI options. "
        "If you touch SUMMARY.md or any nav file, every markdown link target must exist in the existing doc list."
    )

    return "\n".join(parts)


def build_refine_user_prompt(
    pr_context_text: str,
    draft_docs: dict[str, str],
    reviewer_questions: list[str],
    search_terms: list[str],
    matching_docs: list[str],
    touched_paths: set[str],
) -> str:
    """User prompt for a refinement pass after reviewer feedback."""
    matching_preview = ", ".join(matching_docs[:8]) if matching_docs else "see system prompt"
    q_lines = "\n".join(f"{i + 1}. {q}" for i, q in enumerate(reviewer_questions))

    # Include draft content for touched + matching docs (same coverage as initial pass)
    paths_to_show: set[str] = set(touched_paths)
    paths_to_show.update(matching_docs)

    parts = [
        "## Reviewer feedback — address every point in the documentation",
        "",
        q_lines,
        "",
        "Revise the docs so a public reader gets clear, PR-accurate answers. Output full file content in docnerd blocks for each file you change.",
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
        "If you touch SUMMARY.md or nav, every link target must exist in the repo doc set."
    )

    return "\n".join(parts)


def parse_docnerd_response(
    response_text: str,
    existing_paths: set[str],
) -> list[DocEdit]:
    """
    Parse Claude's response to extract file edits.
    Marks as is_new=False for paths that existed.
    """
    edits: list[DocEdit] = []
    # Allow optional whitespace after docnerd: and before newline (models vary)
    patterns = [
        re.compile(r"```docnerd:\s*([^\n\r]+?)\s*\r?\n(.*?)```", re.DOTALL),
        re.compile(r"```\s*docnerd:\s*([^\n\r]+?)\s*\r?\n(.*?)```", re.DOTALL),
    ]
    seen_paths: set[str] = set()
    for pattern in patterns:
        for match in pattern.finditer(response_text):
            path = match.group(1).strip()
            content = match.group(2).strip()
            if path and content and path not in seen_paths:
                seen_paths.add(path)
                is_new = path not in existing_paths
                edits.append(DocEdit(path=path, content=content, is_new=is_new))

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
    ) -> list[DocEdit]:
        """
        Generate documentation edits based on PR context.

        Args:
            pr_context: Analyzed PR context
            target_branch: Target docs branch (e.g. core/v7.1)
            existing_docs: Dict of path -> content for existing docs (required)
            nav_structure: MkDocs nav structure text

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

        system_prompt = build_system_prompt(
            self.rules_text,
            target_branch,
            nav_structure,
            list(existing_paths),
            search_terms,
            matching_docs,
            allow_new_files=allow_new_files,
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

        edits_result = parse_docnerd_response(text, existing_paths)
        if edits_result:
            paths = [e.path for e in edits_result]
            logger.info("Claude response: %d edit(s) to %s", len(edits_result), paths)
        else:
            logger.info("Claude response: no docnerd blocks parsed (raw length %d chars)", len(text))

        review_cfg = review_loop if review_loop is not None else {}
        if review_cfg.get("enabled", True) and edits_result:
            from docnerd.review_loop import run_review_refinement_loop

            max_wall = float(review_cfg.get("max_wall_seconds", 600))
            max_rounds = int(review_cfg.get("max_rounds", 8))
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
