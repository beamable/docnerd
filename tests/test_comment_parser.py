"""Tests for comment parser."""

import pytest
from docnerd.comment_parser import parse_trigger, TriggerMatch


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
