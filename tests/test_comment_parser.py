"""Tests for comment parser."""

import pytest
from docnerd.comment_parser import mentions_docnerd, parse_trigger, TriggerMatch


def test_parse_trigger_matches():
    m = parse_trigger("@docNerd, doc for core/v7.1")
    assert m.matched is True
    assert m.branch == "core/v7.1"


def test_parse_trigger_no_match():
    m = parse_trigger("random comment")
    assert m.matched is False
    assert m.branch is None


def test_parse_trigger_flexible_whitespace():
    m = parse_trigger("@docNerd,  doc for  core/v7.1")
    assert m.matched is True
    assert m.branch == "core/v7.1"


def test_parse_trigger_empty():
    m = parse_trigger("")
    assert m.matched is False


def test_parse_trigger_add_docs_to():
    m = parse_trigger("@docnerd, add docs to core/v7.1")
    assert m.matched is True
    assert m.branch == "core/v7.1"


def test_parse_trigger_add_doc_to():
    m = parse_trigger("@docNerd, add doc to main")
    assert m.matched is True
    assert m.branch == "main"


def test_mentions_docnerd_true():
    assert mentions_docnerd("@docNerd hello") is True
    assert mentions_docnerd("@docnerd, add docs to x") is True
    assert mentions_docnerd("Hey @docNerd what's up") is True


def test_mentions_docnerd_false():
    assert mentions_docnerd("random comment") is False
    assert mentions_docnerd("") is False
    assert mentions_docnerd("   ") is False
