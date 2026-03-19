"""Analyze PR content to extract context for documentation generation."""

from dataclasses import dataclass, field
from github.PullRequest import PullRequest
from github.Repository import Repository


@dataclass
class PRContext:
    """Context extracted from a PR for documentation generation."""

    title: str
    body: str
    number: int
    html_url: str
    head_ref: str
    base_ref: str
    files: list[dict] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    full_contents: dict[str, str] = field(default_factory=dict)  # path -> full content from base


def analyze_pr(
    pr: PullRequest,
    repo: Repository | None = None,
    fetch_full_contents: bool = True,
    max_files: int = 30,
    max_content_per_file: int = 12000,
) -> PRContext:
    """
    Extract PR metadata and file changes for documentation generation.

    Args:
        pr: The GitHub PullRequest object
        repo: Repository (for fetching full file contents). If None, full_contents will be empty.
        fetch_full_contents: If True and repo provided, fetch full file content from base branch

    Returns:
        PRContext with title, body, files, full_contents, etc.
    """
    files = []
    for f in pr.get_files():
        files.append(
            {
                "filename": f.filename,
                "status": f.status,
                "additions": f.additions,
                "deletions": f.deletions,
                "patch": f.patch or "",
            }
        )

    labels = [label.name for label in pr.get_labels()]

    full_contents: dict[str, str] = {}
    if fetch_full_contents and repo:
        from docnerd.github_client import fetch_full_contents_for_pr

        full_contents = fetch_full_contents_for_pr(
            repo, pr, pr.base.ref,
            max_files=max_files,
            max_content_per_file=max_content_per_file,
        )

    return PRContext(
        title=pr.title,
        body=pr.body or "",
        number=pr.number,
        html_url=pr.html_url,
        head_ref=pr.head.ref,
        base_ref=pr.base.ref,
        files=files,
        labels=labels,
        full_contents=full_contents,
    )


def format_pr_context_for_prompt(ctx: PRContext) -> str:
    """Format PR context as text for inclusion in Claude prompt."""
    lines = [
        f"# Source PR: {ctx.title}",
        f"PR #{ctx.number}: {ctx.html_url}",
        f"Branch: {ctx.head_ref} -> {ctx.base_ref}",
        f"Base branch (pre-merge state): {ctx.base_ref}",
        "",
        "## PR Description",
        ctx.body or "(no description)",
        "",
        "## Files Changed",
    ]

    for f in ctx.files:
        lines.append(f"\n### {f['filename']} ({f['status']}, +{f['additions']}/-{f['deletions']})")

        # Full file content from base (for context)
        if f["filename"] in ctx.full_contents:
            lines.append("\n**Full file content (from base branch, before PR):**")
            lines.append("```")
            lines.append(ctx.full_contents[f["filename"]])
            lines.append("```")

        # Diff (what changed)
        if f.get("patch"):
            lines.append("\n**Diff (changes in this PR):**")
            lines.append("```diff")
            lines.append(f["patch"][:8000])  # Limit patch size for context
            if len(f.get("patch", "")) > 8000:
                lines.append("... (truncated)")
            lines.append("```")

    return "\n".join(lines)
