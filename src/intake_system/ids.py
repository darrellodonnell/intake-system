from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone


def content_hash(*parts: object) -> str:
    digest = hashlib.sha256()
    for part in parts:
        if part is None:
            continue
        digest.update(str(part).encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def slugify(value: str, *, fallback: str = "untitled", max_length: int = 80) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)
    return (slug or fallback)[:max_length].strip("-") or fallback


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

