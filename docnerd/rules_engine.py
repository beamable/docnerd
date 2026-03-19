"""Load and format user-defined documentation rules for Claude prompts."""

from pathlib import Path
from typing import Any

import yaml


def load_rules(rules_path: str | Path) -> dict[str, Any]:
    """
    Load all YAML rule files from the rules directory.

    Args:
        rules_path: Path to rules directory (e.g. rules/)

    Returns:
        Dict mapping filename (without .yaml) to rule content
    """
    path = Path(rules_path)
    if not path.exists() or not path.is_dir():
        return {}

    rules: dict[str, Any] = {}
    for f in sorted(path.glob("*.yaml")):
        with open(f) as fp:
            rules[f.stem] = yaml.safe_load(fp) or {}
    for f in sorted(path.glob("*.yml")):
        with open(f) as fp:
            rules[f.stem] = yaml.safe_load(fp) or {}

    return rules


def format_rules_for_prompt(rules: dict[str, Any]) -> str:
    """
    Format rules as text for inclusion in Claude system prompt.

    Args:
        rules: Dict from load_rules()

    Returns:
        Formatted string describing documentation rules
    """
    if not rules:
        return "Follow standard technical documentation best practices."

    lines = [
        "You must follow these documentation rules when generating or editing docs:",
        "",
    ]

    for name, content in rules.items():
        if not content:
            continue
        lines.append(f"### {name.replace('_', ' ').title()} Rules")
        lines.append(_format_rule_content(content))
        lines.append("")

    return "\n".join(lines).strip()


def _format_rule_content(content: Any, indent: int = 0) -> str:
    """Recursively format rule content as readable text."""
    prefix = "  " * indent
    lines = []

    if isinstance(content, dict):
        for k, v in content.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{prefix}- **{k}**:")
                lines.append(_format_rule_content(v, indent + 1))
            else:
                lines.append(f"{prefix}- **{k}**: {v}")
    elif isinstance(content, list):
        for item in content:
            if isinstance(item, (dict, list)):
                lines.append(_format_rule_content(item, indent))
            else:
                lines.append(f"{prefix}- {item}")
    else:
        lines.append(f"{prefix}{content}")

    return "\n".join(lines)
