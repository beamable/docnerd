"""Generate and edit documentation using Claude."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from docnerd.analyzer import PRContext, format_pr_context_for_prompt
from docnerd.rules_engine import format_rules_for_prompt, load_rules


@dataclass
class DocEdit:
    """A single file edit (create or modify)."""

    path: str
    content: str
    is_new: bool


def build_system_prompt(
    rules_text: str,
    target_branch: str,
    nav_structure: str,
    existing_doc_paths: list[str],
) -> str:
    """Build the system prompt with strict doc generation rules."""
    paths_list = "\n".join(f"- {p}" for p in existing_doc_paths[:80])
    return f"""You are a technical documentation maintainer for an MkDocs site. Your job is to integrate PR changes into EXISTING documentation. You must NOT create random new files.

Target branch: {target_branch}

## MkDocs nav structure (these are the existing docs)
```
{nav_structure}
```

## Existing doc files (work within these - do NOT create new files unless strictly necessary)
{paths_list}

## CRITICAL RULES - follow this order

1. **INVALIDATION FIRST**: Does the PR change/remove something that makes existing docs wrong?
   - API changes, config changes, behavior changes, deprecations
   - If yes: EDIT the affected existing doc(s). Fix the incorrect info. Preserve the rest.

2. **ADD CONTEXT**: Does the PR add information that belongs in an existing doc?
   - New options, examples, caveats, migration notes
   - If yes: ADD to the relevant existing doc. Find the right section. Don't create new files.

3. **NEW FILES - RARELY**: Only create a new file if:
   - The PR introduces a major new feature/concept with NO existing doc to add to
   - You have explicit justification
   - Default: DO NOT create new files. When in doubt, add to existing docs or make no changes.

## Output format
For each file you edit, output:
```docnerd:path/to/existing/file.md
<full file content with your edits>
```

- Use EXACT paths from the existing doc list above
- Output the COMPLETE file content (preserve unchanged parts)
- If no doc changes are needed, output nothing (no docnerd blocks)
- Do NOT create files like docs/new-feature.md unless the PR truly warrants a new top-level page

{rules_text}
"""


def build_user_prompt(
    pr_context_text: str,
    existing_docs: dict[str, str],
) -> str:
    """Build the user prompt with PR context and existing docs."""
    parts = [
        "## Your task",
        "",
        "1. Read the PR below.",
        "2. Identify which EXISTING docs (if any) are INVALIDATED by these changes.",
        "3. Identify what NEW CONTEXT should be added to EXISTING docs.",
        "4. Only if the PR introduces a major new feature with no existing doc: consider a new file.",
        "",
        "Output ONLY edits to existing files, or additions to existing files. Prefer editing over creating.",
        "",
        "---",
        "",
        pr_context_text,
        "",
        "---",
        "",
        "## Existing documentation (reference when editing - use these exact paths)",
        "",
    ]

    for path, content in existing_docs.items():
        parts.append(f"### {path}")
        parts.append("```markdown")
        parts.append(content)
        parts.append("```")
        parts.append("")

    parts.append(
        "Respond with docnerd blocks ONLY for files you are editing. "
        "Use the exact path from the list above. Include the full file content with your changes."
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
        system_prompt = build_system_prompt(
            self.rules_text,
            target_branch,
            nav_structure,
            list(existing_paths),
        )
        pr_text = format_pr_context_for_prompt(pr_context)
        user_prompt = build_user_prompt(pr_text, existing_docs)

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

        return parse_docnerd_response(text, existing_paths)
