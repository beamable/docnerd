"""Generate and edit documentation using Claude."""

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


def build_system_prompt(rules_text: str, target_branch: str) -> str:
    """Build the system prompt for Claude with rules and context."""
    return f"""You are a technical documentation writer. You generate and edit Markdown documentation for an MkDocs site.

Target documentation branch: {target_branch}

{rules_text}

When generating docs:
- Create new .md files or edit existing ones as needed to reflect the source code changes
- Use proper Markdown formatting
- Include a note linking back to the source PR when relevant
- For new pages, place them in an appropriate docs/ directory structure
- Preserve existing content when editing; only change what needs to change to reflect the PR
"""


def build_user_prompt(pr_context_text: str, existing_docs: dict[str, str] | None = None) -> str:
    """Build the user prompt with PR context and optional existing docs."""
    parts = [
        "Analyze this pull request and produce documentation changes (new or edited Markdown files).",
        "",
        pr_context_text,
    ]

    if existing_docs:
        parts.append("")
        parts.append("## Existing documentation (for reference when editing)")
        for path, content in existing_docs.items():
            parts.append(f"\n### {path}")
            parts.append("```markdown")
            parts.append(content[:4000] + ("..." if len(content) > 4000 else ""))
            parts.append("```")

    parts.append("")
    parts.append(
        "Respond with your documentation changes. For each file, use this format:\n"
        "```docnerd:path/to/file.md\n"
        "<file content>\n"
        "```\n"
        "You can output multiple files. Use relative paths like docs/guide/feature.md."
    )

    return "\n".join(parts)


def parse_docnerd_response(response_text: str) -> list[DocEdit]:
    """
    Parse Claude's response to extract file edits.

    Expected format:
    ```docnerd:path/to/file.md
    content here
    ```
    """
    import re

    edits: list[DocEdit] = []
    pattern = re.compile(r"```docnerd:([^\n]+)\n(.*?)```", re.DOTALL)

    for match in pattern.finditer(response_text):
        path = match.group(1).strip()
        content = match.group(2).strip()
        if path and content:
            edits.append(DocEdit(path=path, content=content, is_new=True))

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
        existing_docs: dict[str, str] | None = None,
    ) -> list[DocEdit]:
        """
        Generate documentation edits based on PR context.

        Args:
            pr_context: Analyzed PR context
            target_branch: Target docs branch (e.g. core/v7.1)
            existing_docs: Optional dict of path -> content for existing docs to edit

        Returns:
            List of DocEdit (path, content, is_new)
        """
        system_prompt = build_system_prompt(self.rules_text, target_branch)
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

        return parse_docnerd_response(text)
