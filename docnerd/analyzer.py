"""Analyze PR content to extract context for documentation generation."""

from dataclasses import dataclass, field
from github.PullRequest import PullRequest


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


def analyze_pr(pr: PullRequest) -> PRContext:
    """
    Extract PR metadata and file changes for documentation generation.

    Args:
        pr: The GitHub PullRequest object

    Returns:
        PRContext with title, body, files, etc.
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

    return PRContext(
        title=pr.title,
        body=pr.body or "",
        number=pr.number,
        html_url=pr.html_url,
        head_ref=pr.head.ref,
        base_ref=pr.base.ref,
        files=files,
        labels=labels,
    )


def format_pr_context_for_prompt(ctx: PRContext) -> str:
    """Format PR context as text for inclusion in Claude prompt."""
    lines = [
        f"# Source PR: {ctx.title}",
        f"PR #{ctx.number}: {ctx.html_url}",
        f"Branch: {ctx.head_ref} -> {ctx.base_ref}",
        "",
        "## PR Description",
        ctx.body or "(no description)",
        "",
        "## Files Changed",
    ]

    for f in ctx.files:
        lines.append(f"\n### {f['filename']} ({f['status']}, +{f['additions']}/-{f['deletions']})")
        if f.get("patch"):
            lines.append("```diff")
            lines.append(f["patch"][:8000])  # Limit patch size for context
            if len(f.get("patch", "")) > 8000:
                lines.append("... (truncated)")
            lines.append("```")

    return "\n".join(lines)
