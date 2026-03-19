"""GitHub API client for source and target repositories."""

import base64

from github import Github
from github.PullRequest import PullRequest
from github.Repository import Repository


def get_github_client(token: str) -> Github:
    """Create a GitHub client with the given token."""
    return Github(token)


def get_repo(client: Github, owner: str, name: str) -> Repository:
    """Get a repository by owner and name."""
    return client.get_repo(f"{owner}/{name}")


def get_pr(repo: Repository, pr_number: int) -> PullRequest:
    """Get a pull request by number."""
    return repo.get_pull(pr_number)


def get_pr_files(pr: PullRequest) -> list[dict]:
    """Get list of files changed in a PR with patch content."""
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
    return files


def branch_exists(repo: Repository, branch_name: str) -> bool:
    """Check if a branch exists in the repository."""
    try:
        repo.get_branch(branch_name)
        return True
    except Exception:
        return False


def post_comment(pr: PullRequest, body: str) -> None:
    """Post a comment on a pull request (issue)."""
    pr.as_issue().create_comment(body)


def get_file_content(repo: Repository, path: str, ref: str, max_size: int = 50000) -> str | None:
    """
    Get full file content from repo at ref.
    Returns None if file is binary, too large, or not found.
    """
    try:
        content = repo.get_contents(path, ref=ref)
        if content.content is None:
            return None
        raw = base64.b64decode(content.content).decode("utf-8", errors="replace")
        if len(raw) > max_size:
            return raw[:max_size] + "\n... (truncated)"
        return raw
    except Exception:
        return None


def fetch_full_contents_for_pr(
    repo: Repository,
    pr: PullRequest,
    base_ref: str,
    max_files: int = 30,
    max_content_per_file: int = 12000,
    skip_patterns: tuple[str, ...] = ("Models.gs.cs",),
) -> dict[str, str]:
    """
    Fetch full file content from base branch for files changed in the PR.
    Gives Claude context beyond just the diff.
    """
    contents: dict[str, str] = {}
    for f in pr.get_files():
        if len(contents) >= max_files:
            break
        path = f.filename
        # Skip binary, very large, or generated files
        if any(path.endswith(ext) for ext in [".png", ".jpg", ".gif", ".ico", ".pdf", ".bin"]):
            continue
        if any(skip in path for skip in skip_patterns):
            continue
        raw = get_file_content(repo, path, base_ref, max_size=max_content_per_file)
        if raw is not None:
            contents[path] = raw
    return contents
