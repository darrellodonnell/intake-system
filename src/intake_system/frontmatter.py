from __future__ import annotations

from typing import Any

import yaml


class FrontmatterError(ValueError):
    pass


def dumps(frontmatter: dict[str, Any], body: str) -> str:
    yaml_text = yaml.safe_dump(frontmatter, sort_keys=False, allow_unicode=False).strip()
    return f"---\n{yaml_text}\n---\n\n{body.rstrip()}\n"


def loads(text: str) -> tuple[dict[str, Any], str]:
    if not text.startswith("---\n"):
        raise FrontmatterError("Markdown file does not start with YAML frontmatter")
    try:
        _, yaml_text, body = text.split("---", 2)
    except ValueError as exc:
        raise FrontmatterError("Markdown file has incomplete YAML frontmatter") from exc
    data = yaml.safe_load(yaml_text) or {}
    if not isinstance(data, dict):
        raise FrontmatterError("frontmatter must be a mapping")
    return data, body.lstrip("\n")

