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

**Your task:** Add the new option/flag to at least one of the docs above. Prefer docs/cli/, docs/cli/commands/, or deployment guides.

**Writing style:**
- Curt, succinct, powerful. No fluff.
- Explain the *value* of the change—why it matters, not just what it does. (e.g. "Speeds up parallel builds" not just "Sets max parallel count")
- Match the existing voice and tone of each doc you edit.
- One line per option when possible; expand only when necessary.

**You MUST output at least one docnerd block** when the PR adds CLI flags, options, or config. Do NOT output nothing.

## Output format
For each file you edit, output:
```docnerd:path/to/existing/file.md
<full file content with your edits>
```

- Use EXACT paths from the existing doc list
- Output the COMPLETE file content (preserve unchanged parts)
- Only output nothing if the PR has ZERO user-facing changes (pure refactors, generated code only)

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
        "Add the new option/flag to each relevant doc. Include: flag name, default, and the *value* it delivers (why it matters). Keep it curt—match the doc's existing voice.",
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
        "Output docnerd blocks. Use exact paths. Include full file content. You MUST update at least one doc when the PR adds CLI options."
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
    pattern = re.compile(r"```docnerd:([^\n]+)\n(.*?)```", re.DOTALL)

    for match in pattern.finditer(response_text):
        path = match.group(1).strip()
        content = match.group(2).strip()
        if path and content:
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
        matching_docs = compute_matching_docs(list(existing_paths), search_terms)

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

        logger.info("=== System prompt (to Claude) ===")
        logger.info("%s", system_prompt)
        logger.info("=== User prompt (to Claude) ===")
        logger.info("%s", user_prompt)

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

        logger.info("=== Claude response ===")
        logger.info("%s", text)

        return parse_docnerd_response(text, existing_paths)
