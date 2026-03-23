"""Stay under Anthropic message limits: input tokens + max_tokens ≤ context window."""

from __future__ import annotations

import logging
import time
from typing import Any

from anthropic import Anthropic, RateLimitError

from docnerd.docs_fetcher import DOCS_PREVIEW_ONLY_SENTINEL

logger = logging.getLogger("docnerd.llm_context")

API_DEFAULT_MAX_RETRIES = 8
API_DEFAULT_RETRY_BASE_DELAY_S = 2.5


def _is_retryable_api_error(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitError):
        return True
    code = getattr(exc, "status_code", None)
    if code in (429, 503, 529):
        return True
    return False


def messages_create_with_retry(
    client: Anthropic,
    *,
    max_retries: int = API_DEFAULT_MAX_RETRIES,
    base_delay_s: float = API_DEFAULT_RETRY_BASE_DELAY_S,
    **kwargs: Any,
) -> Any:
    """Call client.messages.create with exponential backoff on rate limits."""
    last: BaseException | None = None
    attempts = max(1, max_retries)
    for attempt in range(attempts):
        try:
            return client.messages.create(**kwargs)
        except Exception as e:
            last = e
            if attempt + 1 >= attempts or not _is_retryable_api_error(e):
                raise
            delay = min(base_delay_s * (2**attempt) + 0.2 * attempt, 120.0)
            logger.warning(
                "Anthropic %s (attempt %d/%d); retrying in %.1fs — %s",
                type(e).__name__,
                attempt + 1,
                attempts,
                delay,
                str(e)[:200],
            )
            time.sleep(delay)
    assert last is not None
    raise last

# Pessimistic estimate so we shrink / lower max_tokens before the API returns 400.
# (Anthropic counts can be higher than len/4 for some content.)
CHARS_PER_TOKEN: float = 2.6
INPUT_ESTIMATE_BUFFER = 1.08

DEFAULT_CONTEXT_TOKENS = 200_000
# Slack for tokenizer mismatch vs our estimate
SAFETY_TOKENS = 6_000
MIN_OUTPUT_TOKENS = 2_048
WRITER_MAX_OUTPUT = 16_384
REVIEWER_MAX_OUTPUT = 8_192


def estimate_tokens(*parts: str) -> int:
    total = sum(len(p) for p in parts)
    return max(1, int(total / CHARS_PER_TOKEN))


def compute_max_output_tokens(
    system: str,
    user: str,
    *,
    context_limit: int = DEFAULT_CONTEXT_TOKENS,
    desired_max: int = WRITER_MAX_OUTPUT,
    safety: int = SAFETY_TOKENS,
) -> int:
    """
    max_tokens capped by remaining context. When this falls below MIN_OUTPUT_TOKENS,
    callers should shrink input and retry.
    """
    inp = int(estimate_tokens(system, user) * INPUT_ESTIMATE_BUFFER)
    room = context_limit - inp - safety
    out = min(desired_max, room)
    return max(256, out)


_PREVIEW_NOTICE = (
    f"\n\n---\n*[docnerd: {DOCS_PREVIEW_ONLY_SENTINEL}; "
    "do **not** output a docnerd block for this path]*\n"
)


def shrink_doc_values_for_budget(docs: dict[str, str], max_total_chars: int) -> dict[str, str]:
    """
    Reduce total characters by ~12% per pass until under budget.
    Preserves preview-only sentinel when present so filtering still works.
    """
    out = {k: str(v) for k, v in docs.items()}
    iterations = 0
    while sum(len(v) for v in out.values()) > max_total_chars and iterations < 40:
        iterations += 1
        factor = 0.88
        for k, v in list(out.items()):
            if not v:
                continue
            new_len = max(120, int(len(v) * factor))
            if new_len >= len(v):
                continue
            chunk = v[:new_len]
            if DOCS_PREVIEW_ONLY_SENTINEL in v:
                if DOCS_PREVIEW_ONLY_SENTINEL not in chunk:
                    keep = max(0, new_len - len(_PREVIEW_NOTICE))
                    chunk = v[:keep] + _PREVIEW_NOTICE
                elif new_len < len(v):
                    chunk = chunk + "\n\n... (truncated for context limit — docNerd)"
            else:
                chunk = chunk + "\n\n... (truncated for context limit — docNerd)"
            out[k] = chunk
    if iterations and sum(len(v) for v in out.values()) > max_total_chars:
        logger.warning(
            "Doc shrink stopped after %d iterations; total_chars=%d budget=%d",
            iterations,
            sum(len(v) for v in out.values()),
            max_total_chars,
        )
    return out


def fit_writer_prompt(
    system_prompt: str,
    pr_text: str,
    existing_docs: dict[str, str],
    build_user_prompt,
    search_terms: list[str],
    matching_docs: list[str],
    *,
    context_limit: int = DEFAULT_CONTEXT_TOKENS,
    desired_max_out: int = WRITER_MAX_OUTPUT,
) -> tuple[str, dict[str, str], int]:
    """
    Returns (user_prompt, docs_used_for_prompt, max_tokens).
    Mutates a copy of existing_docs via shrinking until the request fits.
    """
    ed = dict(existing_docs)
    max_tokens = desired_max_out
    user_prompt = ""
    for attempt in range(35):
        user_prompt = build_user_prompt(pr_text, ed, search_terms, matching_docs)
        max_tokens = compute_max_output_tokens(
            system_prompt,
            user_prompt,
            context_limit=context_limit,
            desired_max=desired_max_out,
        )
        if max_tokens >= MIN_OUTPUT_TOKENS:
            if attempt:
                logger.warning(
                    "Shrunk loaded docs to fit API context (%d attempt(s); ~%d chars in docs)",
                    attempt,
                    sum(len(x) for x in ed.values()),
                )
            return user_prompt, ed, max_tokens
        tot = sum(len(x) for x in ed.values())
        if tot < 8_000:
            logger.error(
                "Cannot fit writer prompt in context window; using max_tokens=%s (est. input tokens=%s)",
                max_tokens,
                estimate_tokens(system_prompt, user_prompt),
            )
            return user_prompt, ed, max_tokens
        ed = shrink_doc_values_for_budget(ed, max(8_000, int(tot * 0.86)))

    logger.error(
        "Exhausted doc shrink iterations; est. input tokens=%s max_tokens=%s",
        estimate_tokens(system_prompt, user_prompt),
        max_tokens,
    )
    return user_prompt, ed, max_tokens
