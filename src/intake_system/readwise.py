from __future__ import annotations

from datetime import datetime
from html import unescape
from html.parser import HTMLParser
import re
import time
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, unquote, urlparse, urlunparse

import httpx

from intake_system.ids import content_hash
from intake_system.models import SourceItem
from intake_system.security import redact_sensitive


class ReadwiseClient:
    def __init__(self, *, base_url: str, token: str, page_size: int = 100):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.page_size = page_size

    def iter_items(self, *, cursor: str | None = None) -> Iterator[tuple[list[SourceItem], str | None]]:
        next_cursor = cursor
        with httpx.Client(timeout=30.0) as client:
            while True:
                params: dict[str, Any] = {"pageSize": self.page_size, "withHtmlContent": "true"}
                if next_cursor:
                    params["pageCursor"] = next_cursor
                response = client.get(
                    f"{self.base_url}/list/",
                    headers={"Authorization": f"Token {self.token}"},
                    params=params,
                )
                response.raise_for_status()
                payload = response.json()
                raw_results = payload.get("results", [])
                items = [normalize_readwise_item(raw) for raw in raw_results]
                next_cursor = payload.get("nextPageCursor")
                yield items, next_cursor
                if not next_cursor:
                    break

    def get_raw_item(self, source_id: str, *, max_retries: int = 3) -> dict[str, Any] | None:
        with httpx.Client(timeout=30.0) as client:
            for attempt in range(max_retries + 1):
                response = client.get(
                    f"{self.base_url}/list/",
                    headers={"Authorization": f"Token {self.token}"},
                    params={"id": source_id, "withHtmlContent": "true"},
                )
                if response.status_code != 429:
                    response.raise_for_status()
                    results = response.json().get("results") or []
                    return results[0] if results else None
                if attempt >= max_retries:
                    response.raise_for_status()
                time.sleep(_retry_after_seconds(response))
        return None


def normalize_readwise_item(raw: dict[str, Any], *, source: str = "readwise") -> SourceItem:
    source_id = str(raw.get("id") or raw.get("document_id") or raw.get("url") or content_hash(raw))
    url = canonical_source_url(raw)
    source_type = _source_type(raw, url)
    title = _title(raw, source_id=source_id, url=url, source_type=source_type)
    captured_at = _parse_datetime(
        raw.get("saved_at")
        or raw.get("created_at")
        or raw.get("updated_at")
        or raw.get("last_moved_at")
    )
    content_text = _content_text(raw)
    content_status = "extracted" if content_text else "metadata_only"
    content_error = _content_error(raw, source_type=source_type, content_text=content_text)
    return SourceItem(
        source=source,
        source_id=source_id,
        source_type=source_type,
        title=str(title),
        author=raw.get("author") or raw.get("site_name"),
        source_url=url,
        captured_at=captured_at,
        readwise_tags=_tags(raw.get("tags")),
        raw=redact_sensitive(raw),
        content_text=content_text,
        content_status=content_status,
        content_error=content_error,
        review_priority=_review_priority(source_type, raw),
    )


def canonical_source_url(raw: dict[str, Any]) -> str | None:
    """Return the public source URL, preferring the original URL over Readwise Reader URLs."""
    values = [raw.get("source_url"), raw.get("url"), raw.get("site_url")]
    for value in values:
        if value and not is_readwise_reader_url(str(value)):
            return canonical_public_url(str(value))
    for value in values:
        if value:
            return canonical_public_url(str(value))
    return None


def canonical_public_url(url: str) -> str:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host in {"twitter.com", "www.twitter.com"}:
        parsed = parsed._replace(scheme="https", netloc="x.com")
    query = urlencode(
        [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "__readwiseLocation"]
    )
    parsed = parsed._replace(query=query)
    return urlunparse(parsed)


def _title(raw: dict[str, Any], *, source_id: str, url: str | None, source_type: str) -> str:
    title = raw.get("title") or raw.get("document_title")
    if str(title or "").strip():
        return str(title).strip()
    if source_type == "pdf" and url:
        parsed = urlparse(url)
        iacr_match = re.match(r"^/(\d{4})/(\d+)(?:\.pdf)?$", parsed.path)
        if parsed.netloc.lower() == "eprint.iacr.org" and iacr_match:
            return f"IACR ePrint {iacr_match.group(1)}/{iacr_match.group(2)}"
        filename = unquote(parsed.path.rsplit("/", 1)[-1]).strip()
        if filename:
            return filename
    return url or f"Readwise item {source_id}"


def readwise_reader_url(raw: dict[str, Any]) -> str | None:
    value = raw.get("url")
    if not value:
        return None
    url = str(value)
    if is_readwise_reader_url(url):
        return url
    return None


def is_readwise_reader_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc.lower() == "read.readwise.io" and parsed.path.startswith("/read/")


def _source_type(raw: dict[str, Any], url: str | None) -> str:
    category = str(raw.get("category") or raw.get("source_type") or "").lower()
    if category in {"article", "pdf", "epub", "email", "rss", "tweet", "video"}:
        if category == "video":
            return "youtube" if url and "youtu" in url else "video"
        return "x_twitter" if category == "tweet" else category
    value = f"{url or ''} {raw.get('site_name') or ''}".lower()
    if "youtube.com" in value or "youtu.be" in value:
        return "youtube"
    if "linkedin.com" in value:
        return "linkedin"
    if "twitter.com" in value or "x.com" in value:
        return "x_twitter"
    if raw.get("document_note") or raw.get("notes"):
        return "document"
    return "article" if url else "unknown"


def _tags(raw_tags: Any) -> list[str]:
    if raw_tags is None:
        return []
    if isinstance(raw_tags, list):
        values = raw_tags
    elif isinstance(raw_tags, dict):
        values = list(raw_tags.keys())
    else:
        values = [raw_tags]
    return sorted({str(value).strip() for value in values if str(value).strip()})


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _content_text(raw: dict[str, Any]) -> str | None:
    html_text = _html_to_text(raw.get("html_content"))
    if html_text:
        fields = [html_text, raw.get("notes"), raw.get("document_note")]
    else:
        fields = [
            raw.get("summary"),
            raw.get("notes"),
            raw.get("document_note"),
            raw.get("excerpt"),
        ]
    text = "\n\n".join(str(value).strip() for value in fields if str(value or "").strip())
    return text or None


def _content_error(raw: dict[str, Any], *, source_type: str, content_text: str | None) -> str | None:
    if content_text:
        return None
    error = raw.get("content_error") or raw.get("extraction_error")
    if str(error or "").strip():
        return str(error).strip()
    if source_type == "pdf":
        return "Readwise did not provide extracted PDF text."
    return None


def _html_to_text(value: Any) -> str | None:
    if not value:
        return None
    parser = _PlainTextHTMLParser()
    parser.feed(str(value))
    parser.close()
    text = parser.text()
    return text or None


def _retry_after_seconds(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After")
    if value:
        try:
            return max(float(value), 1.0)
        except ValueError:
            return 10.0
    return 10.0


class _PlainTextHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skipping = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skipping = True
        if tag in {"p", "div", "section", "article", "br", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"}:
            self.skipping = False
        if tag in {"p", "div", "section", "article", "li", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self.skipping:
            self.parts.append(data)

    def text(self) -> str:
        lines = []
        for raw_line in unescape("".join(self.parts)).splitlines():
            line = " ".join(raw_line.split())
            if line:
                lines.append(line)
        return "\n\n".join(lines)


def _review_priority(source_type: str, raw: dict[str, Any]) -> int:
    priority = {
        "linkedin": 85,
        "youtube": 80,
        "x_twitter": 75,
        "document": 70,
        "article": 60,
        "pdf": 60,
    }.get(source_type, 50)
    tags = " ".join(_tags(raw.get("tags"))).lower()
    if any(term in tags for term in ("ayra", "client", "aos", "agent")):
        priority += 10
    return min(priority, 100)
