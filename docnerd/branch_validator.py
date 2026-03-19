"""Validate that requested docs branch exists in target repository."""

from github.Repository import Repository


def validate_branch(repo: Repository.Repository, branch_name: str) -> bool:
    """
    Check if the requested branch exists in the docs repository.

    Args:
        repo: The target docs repository
        branch_name: The branch name requested (e.g. core/v7.1)

    Returns:
        True if branch exists, False otherwise
    """
    try:
        repo.get_branch(branch_name)
        return True
    except Exception:
        return False
