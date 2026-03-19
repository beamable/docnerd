"""Generate and edit documentation using Claude."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from anthropic import Anthropic

from docnerd.analyzer import PRContext, extract_doc_search_terms, format_pr_context_for_prompt
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
    search_terms: list[str],
) -> str:
    """Build the system prompt with strict doc generation rules."""
    paths_list = "\n".join(f"- {p}" for p in existing_doc_paths[:80])
    terms_str = ", ".join(search_terms) if search_terms else "(none extracted)"
    return f"""You are a technical documentation maintainer for an MkDocs site. Your job is to integrate PR changes into EXISTING documentation.

Target branch: {target_branch}

## MkDocs nav structure
```
{nav_structure}
```

## Existing doc files (use EXACT paths from this list)
{paths_list}

## DOC DISCOVERY - you MUST do this first

Search terms extracted from the PR (use these to find relevant docs): {terms_str}

**Matching algorithm:**
1. For EACH search term above, find docs whose path contains that term (case-insensitive).
   - "deploy" matches: docs/Deployment.md, docs/deploy-plan.md, docs/guides/deploy.md
   - "plan" matches: docs/deploy-plan.md, docs/planning.md
   - "build" matches: docs/build.md, docs/BuildProject.md
   - "cli" matches: docs/CLI.md, docs/beam-cli.md
2. Consider partial matches: "deployment" covers "deploy", "DeployPlan" covers "deploy" and "plan"
3. The docs to update = any doc that could document the changed commands/features
4. If the PR changes deploy plan, deploy release, or build commands: docs about deployment, CLI, or build MUST be updated

**You MUST update at least one doc when the PR adds:**
- New CLI flags (e.g. --max-parallel-count)
- New command options
- New config settings (e.g. MaxParallelBuildCount)
- API or interface changes

## Output format
For each file you edit, output:
```docnerd:path/to/existing/file.md
<full file content with your edits>
```

- Use EXACT paths from the existing doc list
- Output the COMPLETE file content (preserve unchanged parts)
- Only output nothing if the PR has ZERO user-facing changes (pure refactors, generated code only)
- Do NOT conclude "no changes needed" when the PR adds CLI flags, options, or config - find a doc and add them

{rules_text}
"""


def build_user_prompt(
    pr_context_text: str,
    existing_docs: dict[str, str],
    search_terms: list[str],
) -> str:
    """Build the user prompt with PR context and existing docs."""
    parts = [
        "## Step 1: Identify docs to update",
        "",
        f"Search terms from this PR: {', '.join(search_terms) or 'deploy, plan, release, build, cli'}",
        "",
        "Which existing docs (from the list below) have paths that match these terms?",
        "Example: If the PR adds --max-parallel-count to beam deploy plan, look for docs containing 'deploy', 'plan', 'release', 'build', 'cli', 'command'.",
        "List the matching doc paths, then add the new option/flag to each.",
        "",
        "## Step 2: Add the documentation",
        "",
        "For each matching doc: add a section or update the options table to document the new flag/option.",
        "Include: flag name, default value, and what it does.",
        "",
        "---",
        "",
        pr_context_text,
        "",
        "---",
        "",
        "## Existing documentation (search these for matches - use EXACT paths)",
        "",
    ]

    for path, content in existing_docs.items():
        parts.append(f"### {path}")
        parts.append("```markdown")
        parts.append(content)
        parts.append("```")
        parts.append("")

    parts.append(
        "Output docnerd blocks for each doc you edit. Use the exact path. Include the full file content with your changes. "
        "If the PR adds CLI options, you MUST update at least one doc - do not output nothing."
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
        search_terms = extract_doc_search_terms(pr_context)
        system_prompt = build_system_prompt(
            self.rules_text,
            target_branch,
            nav_structure,
            list(existing_paths),
            search_terms,
        )
        pr_text = format_pr_context_for_prompt(pr_context)
        user_prompt = build_user_prompt(pr_text, existing_docs, search_terms)

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
