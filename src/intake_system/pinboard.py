from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx

from intake_system.ids import content_hash
from intake_system.models import SourceItem
from intake_system.readwise import canonical_public_url
from intake_system.security import redact_sensitive


class PinboardClient:
    def __init__(self, *, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def bookmarks(self, *, since: str | None = None, limit: int | None = None) -> list[SourceItem]:
        params: dict[str, str] = {
            "auth_token": self.token,
            "format": "json",
            "meta": "yes",
        }
        if since:
            params["fromdt"] = since
        with httpx.Client(timeout=30.0) as client:
            response = client.get(f"{self.base_url}/posts/all", params=params)
            response.raise_for_status()
            payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Pinboard posts/all response must be a JSON list")
        if limit is not None:
            payload = payload[:limit]
        return [normalize_pinboard_bookmark(raw) for raw in payload]


def normalize_pinboard_bookmark(raw: dict[str, Any]) -> SourceItem:
    href = str(raw.get("href") or "").strip()
    url = canonical_public_url(href) if href else None
    source_id = str(raw.get("hash") or raw.get("meta") or href or content_hash(raw))
    title = str(raw.get("description") or raw.get("title") or url or f"Pinboard bookmark {source_id}").strip()
    extended = str(raw.get("extended") or "").strip()
    tags = _tags(raw.get("tags"))
    content_text = _content_text(extended, tags)
    return SourceItem(
        source="pinboard",
        source_id=source_id,
        source_type="bookmark",
        title=title,
        author=None,
        source_url=url,
        captured_at=_parse_datetime(raw.get("time")),
        readwise_tags=tags,
        raw=redact_sensitive(raw),
        content_text=content_text,
        content_status="extracted" if content_text else "metadata_only",
        review_priority=65,
    )


def _content_text(extended: str, tags: list[str]) -> str | None:
    parts = []
    if extended:
        parts.append(f"Pinboard note: {extended}")
    if tags:
        parts.append(f"Pinboard tags: {', '.join(tags)}")
    return "\n\n".join(parts) or None


def _tags(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, list):
        values = value
    else:
        values = str(value).split()
    return sorted({str(tag).strip() for tag in values if str(tag).strip()})


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
