"""Tests for markdown path prioritization when capping doc fetch count."""

from docnerd.docs_fetcher import _prioritize_md_paths
from docnerd.doc_generator import parse_docnerd_response


def test_prioritize_prefers_paths_matching_pr_terms():
    many_early = [f"docs/appendix-{i:03d}.md" for i in range(55)]
    cli_deploy = "docs/cli/guides/ms-deployment.md"
    paths = many_early + [cli_deploy]
    terms = ["deploy", "cli", "docker", "build"]
    picked = _prioritize_md_paths(paths, terms, max_files=50)
    assert cli_deploy in picked, "alphabetical cap would drop cli/; prioritize must keep it"


def test_parse_docnerd_allows_space_after_colon():
    text = """Here you go:
```docnerd: docs/foo.md
# Hello
content
```
"""
    edits = parse_docnerd_response(text, {"docs/foo.md"})
    assert len(edits) == 1
    assert edits[0].path == "docs/foo.md"
