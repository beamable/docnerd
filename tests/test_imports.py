"""Test that all docnerd modules import without error."""

import pytest


def test_import_all_modules():
    """Import every module to catch AttributeError and similar at import time."""
    from docnerd import comment_parser
    from docnerd import config
    from docnerd import rules_engine
    from docnerd.analyzer import analyze_pr, PRContext, extract_doc_search_terms, format_pr_context_for_prompt
    from docnerd.doc_generator import ensure_matching_docs
    from docnerd.branch_validator import validate_branch
    from docnerd.comment_parser import parse_trigger, TriggerMatch
    from docnerd.doc_generator import DocGenerator, DocEdit, parse_docnerd_response
    from docnerd.docs_fetcher import fetch_existing_docs, get_mkdocs_config
    from docnerd.github_client import (
        get_github_client,
        get_repo,
        get_pr,
        get_pr_files,
        branch_exists,
        post_comment,
    )
    from docnerd.pr_creator import (
        create_docs_pr,
        find_existing_docs_pr,
        make_work_branch_name,
    )
    from docnerd.rules_engine import load_rules, format_rules_for_prompt
    from docnerd.llm_context import compute_max_output_tokens, fit_writer_prompt
    from docnerd.docnerd_cache import dump_cache_yaml
    from docnerd.phased_pipeline import run_phased_generation
    from docnerd.review_loop import parse_reviewer_response, apply_edits_to_draft
    from docnerd.main import run, main


def test_main_module_runs():
    """Run main() with fake env - should exit early (no real API calls)."""
    import os

    # Set env that will cause parse_trigger to return no match
    os.environ["COMMENT_BODY"] = "hello world"
    os.environ["PR_NUMBER"] = "1"
    os.environ["SOURCE_OWNER"] = "test"
    os.environ["SOURCE_NAME"] = "test-repo"

    from docnerd.main import run

    # Should return 0 (no match, no trigger)
    result = run(
        comment_body="hello world",
        pr_number=1,
        source_owner="test",
        source_name="test-repo",
    )
    assert result == 0
