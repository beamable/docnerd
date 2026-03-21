"""Tests for Anthropic context budgeting."""

from docnerd.llm_context import (
    compute_max_output_tokens,
    estimate_tokens,
    fit_writer_prompt,
    shrink_doc_values_for_budget,
)


def test_estimate_tokens_positive():
    assert estimate_tokens("abc") >= 1
    assert estimate_tokens("a" * 3000) > 1000


def test_compute_max_output_tokens_respects_room():
    tiny_user = "x" * 1000
    huge = "y" * 500_000
    system = "system"
    out_small = compute_max_output_tokens(system, tiny_user, desired_max=16_384)
    assert out_small == 16_384
    out_huge = compute_max_output_tokens(system, huge, desired_max=16_384)
    assert out_huge < 16_384


def test_shrink_doc_values_reduces_total():
    docs = {"a.md": "w" * 10_000, "b.md": "z" * 10_000}
    shrunk = shrink_doc_values_for_budget(docs, 8_000)
    assert sum(len(v) for v in shrunk.values()) <= 8_000 + 500  # footer slack


def test_fit_writer_prompt_shrinks_when_huge():
    huge_docs = {f"f{i}.md": "p" * 50_000 for i in range(20)}

    def build_user(pr, ed, st, md):
        return pr + "\n" + "\n".join(ed.values())

    pr = "pr " * 2000
    system = "sys " * 5000
    user, ed, mt = fit_writer_prompt(
        system,
        pr,
        huge_docs,
        build_user,
        [],
        [],
    )
    assert len(user) < sum(len(x) for x in huge_docs.values())
    assert mt >= 1024
