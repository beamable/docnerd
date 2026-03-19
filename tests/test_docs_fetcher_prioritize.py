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


def test_parse_docnerd_nested_markdown_code_fence_does_not_truncate():
    """Regression: inner ``` must not end the block early (old regex used .*?```)."""
    text = """```docnerd:docs/cli/guide.md
# Guide
Run this:

```csharp
Foo();
```

More text after inner fence.
```docnerd:docs/cli/other.md
Second file only.
"""
    edits = parse_docnerd_response(text, {"docs/cli/guide.md", "docs/cli/other.md"})
    assert len(edits) == 2
    assert "csharp" in edits[0].content
    assert "More text after inner fence" in edits[0].content
    assert "Second file" in edits[1].content


def test_parse_docnerd_case_insensitive_opening():
    text = """```DocNerd:docs/x.md
body
"""
    edits = parse_docnerd_response(text, {"docs/x.md"})
    assert len(edits) == 1
    assert edits[0].content == "body"


def test_parse_docnerd_json_fallback():
    text = r"""
Here is JSON:
```json
{"docnerd_files": [{"path": "docs/a.md", "content": "# A\nline"}]}
```
"""
    edits = parse_docnerd_response(text, {"docs/a.md"})
    assert len(edits) == 1
    assert edits[0].path == "docs/a.md"
    assert "# A" in edits[0].content
