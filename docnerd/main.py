"""Main orchestration: handle comment trigger and run full docNerd flow."""

import logging
import os
import sys
from pathlib import Path

from docnerd.analyzer import analyze_pr
from docnerd.branch_validator import validate_branch
from docnerd.comment_parser import parse_trigger
from docnerd.config import load_config
from docnerd.doc_generator import DocGenerator
from docnerd.docs_fetcher import fetch_existing_docs
from docnerd.github_client import get_github_client, get_pr, get_repo, post_comment
from docnerd.pr_creator import create_docs_pr, find_existing_docs_pr

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("docnerd")

DOCNERD_PREFIX = "I am docNerd. "


def _comment(pr, msg: str) -> None:
    """Post a comment with docNerd identification."""
    post_comment(pr, DOCNERD_PREFIX + msg)


def run(
    comment_body: str,
    pr_number: int,
    source_owner: str,
    source_name: str,
    config_path: str | Path | None = None,
) -> int:
    """
    Main entry point: process a comment on a source PR.

    Args:
        comment_body: The comment text
        pr_number: Source PR number
        source_owner: Source repo owner
        source_name: Source repo name
        config_path: Optional path to config file

    Returns:
        0 on success, 1 on failure
    """
    config = load_config(config_path)
    trigger_phrase = config.get("trigger_phrase", "@docNerd, doc for")

    match = parse_trigger(comment_body, trigger_phrase)
    if not match.matched or not match.branch:
        logger.info("Comment does not match trigger, skipping")
        return 0

    target_branch = match.branch
    logger.info("Trigger matched: target branch=%s", target_branch)

    # GitHub tokens
    source_token = config.get("source_repo", {}).get("token") or os.getenv("GITHUB_TOKEN")
    target_token = config.get("target_repo", {}).get("token") or os.getenv("TARGET_REPO_TOKEN") or source_token

    if not source_token:
        logger.error("No GitHub token configured")
        return 1

    gh = get_github_client(source_token)
    source_repo = get_repo(gh, source_owner, source_name)
    pr = get_pr(source_repo, pr_number)

    # Target repo
    target_owner = config.get("target_repo", {}).get("owner")
    target_name = config.get("target_repo", {}).get("name")
    if not target_owner or not target_name:
        logger.error("Target repo not configured (target_repo.owner, target_repo.name)")
        _comment(pr, "I couldn't complete the request: target docs repository is not configured.")
        return 1

    target_repo = get_repo(gh, target_owner, target_name) if target_token == source_token else get_repo(
        get_github_client(target_token), target_owner, target_name
    )

    # Validate branch exists
    if not validate_branch(target_repo, target_branch):
        logger.warning("Branch %s not found in docs repo", target_branch)
        _comment(pr, "I couldn't find that branch.")
        return 0

    # Reply "working on it"
    _comment(pr, "yes, working on it")

    # Check for existing PR (idempotency)
    branch_prefix = config.get("branch_prefix", "docnerd")
    existing_pr_url = find_existing_docs_pr(target_repo, pr_number, target_branch, branch_prefix)
    if existing_pr_url:
        logger.info("Found existing docs PR: %s", existing_pr_url)
        _comment(pr, f"Here is the link to the Doc changes: {existing_pr_url}")
        return 0

    # Analyze PR
    pr_context = analyze_pr(pr)

    # LLM config
    llm_config = config.get("llm", {})
    api_key = llm_config.get("api_key")
    if not api_key:
        logger.error("No Anthropic API key configured")
        _comment(pr, "I ran into an error generating docs: LLM API key is not configured.")
        return 1

    # Generate docs - resolve rules_path from workspace (source repo), fallback to action's rules
    rules_path = config.get("rules_path", "rules")
    workspace = Path.cwd()
    rules_full = Path(rules_path) if Path(rules_path).is_absolute() else workspace / rules_path
    if not rules_full.exists():
        action_path = os.getenv("GITHUB_ACTION_PATH")
        if action_path:
            rules_full = Path(action_path) / rules_path

    # Fetch existing docs from target repo (required for integration)
    try:
        existing_docs, nav_structure = fetch_existing_docs(
            target_repo,
            ref=target_branch,
            max_files=config.get("docs_fetcher", {}).get("max_files", 50),
            max_content_per_file=config.get("docs_fetcher", {}).get("max_content_per_file", 8000),
        )
    except Exception as e:
        logger.warning("Could not fetch existing docs: %s. Proceeding without.", e)
        existing_docs = {}
        nav_structure = "(could not fetch)"

    if not existing_docs:
        logger.warning("No existing docs found in target repo. Doc generation may create new files.")

    try:
        generator = DocGenerator(
            api_key=api_key,
            model=llm_config.get("model", "claude-sonnet-4-20250514"),
            base_url=llm_config.get("base_url"),
            rules_path=rules_full,
        )
        edits = generator.generate(
            pr_context,
            target_branch,
            existing_docs=existing_docs,
            nav_structure=nav_structure,
        )
    except Exception as e:
        logger.exception("Doc generation failed")
        _comment(pr, f"I ran into an error generating docs: {e!s}")
        return 1

    if not edits:
        _comment(pr, "I reviewed the PR but didn't find documentation changes that needed to be made.")
        return 0

    # Optionally filter out new files (config: allow_new_files: false)
    allow_new = config.get("allow_new_files", True)
    if not allow_new:
        edits = [e for e in edits if not e.is_new]
        if not edits:
            _comment(pr, "I found potential doc updates but they would require new files. With allow_new_files disabled, no changes were made.")
            return 0

    # Create docs PR
    try:
        docs_pr_url = create_docs_pr(
            repo=target_repo,
            target_branch=target_branch,
            source_pr_number=pr_number,
            source_pr_url=pr_context.html_url,
            source_pr_title=pr_context.title,
            edits=edits,
            branch_prefix=config.get("branch_prefix", "docnerd"),
            token=target_token,
        )
    except Exception as e:
        logger.exception("Failed to create docs PR")
        _comment(pr, f"I ran into an error creating the docs PR: {e!s}")
        return 1

    _comment(pr, f"Here is the link to the Doc changes: {docs_pr_url}")
    logger.info("Done. Docs PR: %s", docs_pr_url)
    return 0


def main() -> int:
    """CLI entry point. Expects env vars from GitHub Actions context."""
    comment_body = os.getenv("COMMENT_BODY", "")
    pr_number_str = os.getenv("PR_NUMBER", "")
    source_owner = os.getenv("SOURCE_OWNER", "")
    source_name = os.getenv("SOURCE_NAME", "")

    # Fallback: derive from GITHUB_REPOSITORY (owner/repo)
    if not source_owner or not source_name:
        repo = os.getenv("GITHUB_REPOSITORY", "")
        if "/" in repo:
            parts = repo.split("/", 1)
            source_owner = source_owner or parts[0]
            source_name = source_name or parts[1]

    if not all([comment_body, pr_number_str, source_owner, source_name]):
        logger.error("Missing env vars: COMMENT_BODY, PR_NUMBER, SOURCE_OWNER/SOURCE_NAME (or GITHUB_REPOSITORY)")
        return 1

    try:
        pr_number = int(pr_number_str)
    except ValueError:
        logger.error("PR_NUMBER must be an integer")
        return 1

    return run(comment_body, pr_number, source_owner, source_name)


if __name__ == "__main__":
    sys.exit(main())
