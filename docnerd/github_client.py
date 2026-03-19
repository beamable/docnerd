"""GitHub API client for source and target repositories."""

from github import Github
from github.PullRequest import PullRequest
from github.Repository import Repository


def get_github_client(token: str) -> Github:
    """Create a GitHub client with the given token."""
    return Github(token)


def get_repo(client: Github, owner: str, name: str) -> Repository.Repository:
    """Get a repository by owner and name."""
    return client.get_repo(f"{owner}/{name}")


def get_pr(repo: Repository.Repository, pr_number: int) -> PullRequest.PullRequest:
    """Get a pull request by number."""
    return repo.get_pull(pr_number)


def get_pr_files(pr: PullRequest.PullRequest) -> list[dict]:
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


def branch_exists(repo: Repository.Repository, branch_name: str) -> bool:
    """Check if a branch exists in the repository."""
    try:
        repo.get_branch(branch_name)
        return True
    except Exception:
        return False


def post_comment(pr: PullRequest.PullRequest, body: str) -> None:
    """Post a comment on a pull request (issue)."""
    pr.as_issue().create_comment(body)
