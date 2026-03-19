"""Tests for reviewer/refinement loop helpers."""

from docnerd.doc_generator import DocEdit
from docnerd.review_loop import (
    apply_edits_to_draft,
    draft_to_final_edits,
    parse_reviewer_response,
)


def test_parse_reviewer_satisfied_json_block():
    text = """Here is my verdict:
```json
{"status": "satisfied"}
```
"""
    sat, qs = parse_reviewer_response(text)
    assert sat is True
    assert qs == []


def test_parse_reviewer_needs_revision():
    text = """```json
{"status": "needs_revision", "questions": ["What is the default?", "Any caveats?"]}
```"""
    sat, qs = parse_reviewer_response(text)
    assert sat is False
    assert len(qs) == 2
    assert "default" in qs[0]


def test_apply_and_draft_to_final():
    base = {"a.md": "old", "b.md": "bee"}
    edits = [DocEdit(path="a.md", content="new", is_new=False)]
    d = apply_edits_to_draft(base, edits)
    assert d["a.md"] == "new"
    assert d["b.md"] == "bee"
    final = draft_to_final_edits(base, d)
    assert len(final) == 1
    assert final[0].path == "a.md"
    assert final[0].content == "new"
