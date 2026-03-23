"""Tests for DOCNERD_CACHE.yml helpers."""

from docnerd.docnerd_cache import CACHE_VERSION, dump_cache_yaml, load_cache_from_repo


def test_dump_and_parse_roundtrip_structure():
    data = {
        "version": CACHE_VERSION,
        "files": {
            "docs/a.md": {
                "content_sha": "abc",
                "description_updated_at": "2026-01-01T00:00:00+00:00",
                "source_last_modified_at": "2026-01-01T00:00:00+00:00",
                "description": "Covers topic A.",
            }
        },
    }
    yml = dump_cache_yaml(data)
    assert "DOCNERD_CACHE.yml" in yml
    assert "docs/a.md" in yml
    assert "content_sha" in yml


def test_load_cache_from_repo_missing_returns_empty(monkeypatch):
    class R:
        def get_contents(self, path, ref):
            raise Exception("missing")

    empty = load_cache_from_repo(R(), "main", "DOCNERD_CACHE.yml")
    assert empty["version"] == CACHE_VERSION
    assert empty["files"] == {}
