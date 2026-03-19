"""Tests for PR → doc search term extraction and fallback matching."""

from docnerd.analyzer import PRContext, extract_doc_search_terms
from docnerd.doc_generator import ensure_matching_docs


def test_extract_terms_from_docker_msbuild_pr():
    ctx = PRContext(
        title="Fix CLI overriding Dockerfile BEAM_DOTNET_VERSION with hardcoded alpine tag",
        body="Set ContainerFamily in csproj. docker buildx uses BEAM_DOTNET_VERSION.",
        number=4539,
        html_url="https://example.com/pr/4539",
        head_ref="fix",
        base_ref="main",
        files=[{"filename": "cli/ServicesBuildCommand.cs", "status": "modified", "additions": 1, "deletions": 0, "patch": ""}],
    )
    terms = extract_doc_search_terms(ctx)
    assert "docker" in terms
    assert "dockerfile" in terms or "container" in terms
    assert "cli" in terms
    assert "build" in terms


def test_ensure_matching_docs_fallback_when_terms_signal_user_facing():
    doc_paths = [
        "docs/cli/guides/ms-deployment.md",
        "docs/random/internal.md",
        "docs/cli/commands/cli-command-reference.md",
    ]
    # Terms imply deploy/docker but no substring matched paths
    search_terms = ["dockerfile", "container", "msbuild", "csproj", "noble"]
    raw = []  # simulate no path containing "noble"
    matched = ensure_matching_docs(doc_paths, raw, search_terms)
    assert matched
    assert any("cli" in p for p in matched)
    assert any("deploy" in p or "ms-" in p for p in matched)
