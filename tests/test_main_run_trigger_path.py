"""
Regression: run() must execute past extract_doc_search_terms(pr_context) when a trigger matches.

A redundant `from docnerd.analyzer import extract_doc_search_terms` inside `if not edits:`
made that name local for the whole function, causing UnboundLocalError at fetch time.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from docnerd.analyzer import PRContext


_MINIMAL_CONFIG = {
    "trigger_phrase": "@docNerd, doc for",
    "branch_prefix": "docnerd",
    "source_repo": {"token": "fake-source-token"},
    "target_repo": {
        "owner": "beamable",
        "name": "docs",
        "token": "fake-target-token",
    },
    "llm": {"api_key": "fake-anthropic-key", "model": "claude-3-haiku-20240307"},
    "docs_fetcher": {"max_files": 10, "max_content_per_file": 1000},
    "pr_analysis": {"max_files": 5, "fetch_full_contents": False},
    "doc_review_loop": {"enabled": False},
    "allow_new_files": True,
}


@patch("docnerd.main.create_docs_pr")
@patch("docnerd.main.DocGenerator")
@patch("docnerd.main.fetch_existing_docs")
@patch("docnerd.main.analyze_pr")
@patch("docnerd.main.find_existing_docs_pr")
@patch("docnerd.main.validate_branch", return_value=True)
@patch("docnerd.main.post_comment")
@patch("docnerd.main.get_pr")
@patch("docnerd.main.get_repo")
@patch("docnerd.main.get_github_client")
@patch("docnerd.main.load_config")
def test_run_trigger_path_calls_fetch_and_extract_terms_without_error(
    mock_load_config,
    mock_get_github_client,
    mock_get_repo,
    mock_get_pr,
    mock_post_comment,
    mock_validate_branch,
    mock_find_existing_docs_pr,
    mock_analyze_pr,
    mock_fetch_existing_docs,
    mock_doc_generator_class,
    mock_create_docs_pr,
) -> None:
    mock_load_config.return_value = _MINIMAL_CONFIG.copy()
    mock_get_pr.return_value = MagicMock()
    mock_get_repo.return_value = MagicMock()
    mock_find_existing_docs_pr.return_value = None

    mock_analyze_pr.return_value = PRContext(
        title="Docker / MSBuild change",
        body="ContainerFamily csproj docker build",
        number=99,
        html_url="https://example.com/pr/99",
        head_ref="feature",
        base_ref="main",
        files=[
            {
                "filename": "cli/ServicesBuildCommand.cs",
                "status": "modified",
                "additions": 2,
                "deletions": 0,
                "patch": "",
            }
        ],
    )
    mock_fetch_existing_docs.return_value = (
        {"docs/cli/guide.md": "# Guide\n"},
        "(nav)",
        ["docs/cli/guide.md"],
    )

    gen = MagicMock()
    gen.generate.return_value = []
    mock_doc_generator_class.return_value = gen

    from docnerd.main import run

    rc = run(
        comment_body="@docNerd, doc for core/v7.1",
        pr_number=1,
        source_owner="beamable",
        source_name="BeamableProduct",
        config_path=None,
    )

    assert rc == 0
    mock_analyze_pr.assert_called_once()
    mock_fetch_existing_docs.assert_called_once()
    _args, kwargs = mock_fetch_existing_docs.call_args
    assert "prioritize_terms" in kwargs
    assert kwargs["prioritize_terms"], "search terms should be non-empty for this PR context"
    mock_doc_generator_class.assert_called_once()
    gen.generate.assert_called_once()
    assert gen.generate.call_args.kwargs.get("all_doc_paths") == ["docs/cli/guide.md"]
    # No docs PR when generate returns []
    mock_create_docs_pr.assert_not_called()
