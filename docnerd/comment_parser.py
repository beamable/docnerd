"""Parse comments to detect docNerd trigger and extract target branch."""

import re
from dataclasses import dataclass


@dataclass
class TriggerMatch:
    """Result of parsing a comment for a docNerd trigger."""

    matched: bool
    branch: str | None = None
    raw_comment: str = ""


# Default trigger: docNerd, doc for <branch> OR docNerd, add docs to <branch>
# No leading @ — avoids GitHub user mentions (there is a @docNerd account).
# (?<!@) blocks matching when someone still types @docNerd (no trigger → no ping in our parser).
# Branch can contain letters, numbers, slashes, dots, hyphens
DEFAULT_PATTERN = re.compile(
    r"(?i)(?<!@)docNerd\s*,\s*(?:doc\s+for|add\s+docs?\s+to)\s+([\w./\-]+)",
)


def parse_trigger(comment_body: str, trigger_phrase: str | None = None) -> TriggerMatch:
    """
    Parse a comment to detect if it's a docNerd trigger and extract the target branch.

    Args:
        comment_body: The raw comment text
        trigger_phrase: Optional custom trigger prefix (e.g. "docNerd, doc for").
                        If provided, we build a pattern from it.

    Returns:
        TriggerMatch with matched=True and branch set if trigger found, else matched=False
    """
    if not comment_body or not comment_body.strip():
        return TriggerMatch(matched=False, raw_comment=comment_body)

    if trigger_phrase:
        # Build pattern: trigger_phrase + branch (word chars, slashes, dots, hyphens)
        escaped = re.escape(trigger_phrase.strip())
        pattern = re.compile(rf"{escaped}\s+([\w./\-]+)", re.IGNORECASE)
    else:
        pattern = DEFAULT_PATTERN

    match = pattern.search(comment_body)
    if match:
        branch = match.group(1).strip()
        if branch:
            return TriggerMatch(matched=True, branch=branch, raw_comment=comment_body)

    return TriggerMatch(matched=False, raw_comment=comment_body)


def mentions_docnerd(comment_body: str) -> bool:
    """True if the user seems to be talking to docNerd (plain name or legacy @ mention)."""
    if not comment_body or not comment_body.strip():
        return False
    lower = comment_body.lower()
    if "@docnerd" in lower:
        return True
    # Plain "docNerd" / "docnerd" as a word (not a substring of another token)
    return bool(re.search(r"(?i)(?<![@\w])docnerd(?![\w])", comment_body))
