from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import re
import time

import psycopg

from intake_system.frontmatter import dumps, loads
from intake_system.readwise import ReadwiseClient, canonical_source_url, normalize_readwise_item, readwise_reader_url


@dataclass
class SourceUrlRepairResult:
    scanned: int = 0
    updated_items: int = 0
    updated_review_notes: int = 0
    updated_staged_files: int = 0
    missing_staged_files: int = 0


@dataclass
class ContentRefreshResult:
    scanned: int = 0
    fetched: int = 0
    updated_items: int = 0
    updated_staged_files: int = 0
    missing_staged_files: int = 0
    no_content: int = 0


def refresh_readwise_content(
    conn: psycopg.Connection,
    client: ReadwiseClient,
    *,
    item_ids: list[int] | None = None,
    request_delay: float = 0.0,
    dry_run: bool = True,
) -> ContentRefreshResult:
    params: list[object] = []
    item_filter = ""
    if item_ids:
        item_filter = "AND i.id = ANY(%s)"
        params.append(item_ids)
    rows = conn.execute(
        f"""
        SELECT i.id, i.source_id, i.content_text, r.staged_path
        FROM intake.items i
        LEFT JOIN intake.review_notes r ON r.item_id = i.id
        WHERE i.source = 'readwise'
          {item_filter}
        ORDER BY i.id
        """,
        params,
    ).fetchall()
    result = ContentRefreshResult(scanned=len(rows))
    for row in rows:
        raw = client.get_raw_item(str(row["source_id"]))
        if request_delay > 0:
            time.sleep(request_delay)
        if raw is None:
            continue
        result.fetched += 1
        item = normalize_readwise_item(raw)
        if not item.content_text:
            result.no_content += 1
            continue
        if item.content_text != row["content_text"]:
            result.updated_items += 1
            if not dry_run:
                from intake_system.db import IntakeRepository

                IntakeRepository(conn).upsert_item(item)
        staged_path = row["staged_path"]
        if staged_path:
            path = Path(staged_path)
            if not path.exists():
                result.missing_staged_files += 1
                continue
            current = path.read_text()
            repaired = repair_staged_extracted_context(current, content_text=item.content_text)
            if repaired != current:
                result.updated_staged_files += 1
                if not dry_run:
                    path.write_text(repaired)
    return result


def repair_readwise_source_urls(conn: psycopg.Connection, *, dry_run: bool = True) -> SourceUrlRepairResult:
    rows = conn.execute(
        """
        SELECT i.id, i.source_url, i.raw, r.staged_path, r.frontmatter
        FROM intake.items i
        LEFT JOIN intake.review_notes r ON r.item_id = i.id
        WHERE i.source = 'readwise'
          AND coalesce(i.raw->>'source_url', '') <> ''
        ORDER BY i.id
        """
    ).fetchall()
    result = SourceUrlRepairResult(scanned=len(rows))
    for row in rows:
        raw = dict(row["raw"] or {})
        repaired_url = canonical_source_url(raw)
        if not repaired_url or row["source_url"] == repaired_url:
            continue

        result.updated_items += 1
        reader_url = readwise_reader_url(raw)
        if not dry_run:
            conn.execute(
                """
                UPDATE intake.items
                SET source_url = %s, updated_at = now()
                WHERE id = %s
                """,
                (repaired_url, row["id"]),
            )

        frontmatter = dict(row["frontmatter"] or {})
        if frontmatter:
            frontmatter = repair_source_frontmatter(frontmatter, source_url=repaired_url, readwise_url=reader_url)
            result.updated_review_notes += 1
            if not dry_run:
                conn.execute(
                    """
                    UPDATE intake.review_notes
                    SET frontmatter = %s::jsonb, updated_at = now()
                    WHERE item_id = %s
                    """,
                    (json.dumps(frontmatter), row["id"]),
                )

        staged_path = row["staged_path"]
        if staged_path:
            path = Path(staged_path)
            if not path.exists():
                result.missing_staged_files += 1
                continue
            current = path.read_text()
            repaired = repair_staged_markdown_text(
                current,
                source_url=repaired_url,
                readwise_url=reader_url,
            )
            if repaired != current:
                result.updated_staged_files += 1
                if not dry_run:
                    path.write_text(repaired)
    return result


def repair_source_frontmatter(
    frontmatter: dict,
    *,
    source_url: str,
    readwise_url: str | None,
) -> dict:
    repaired = dict(frontmatter)
    source = dict(repaired.get("source") or {})
    source["url"] = source_url
    if readwise_url and readwise_url != source_url:
        source["readwise_url"] = readwise_url
    else:
        source.pop("readwise_url", None)
    repaired["source"] = source
    return repaired


def repair_staged_markdown_text(
    markdown_text: str,
    *,
    source_url: str,
    readwise_url: str | None,
) -> str:
    frontmatter, body = loads(markdown_text)
    frontmatter = repair_source_frontmatter(frontmatter, source_url=source_url, readwise_url=readwise_url)
    body = re.sub(r"(?m)^Source: .*$", f"Source: {source_url}", body, count=1)
    return dumps(frontmatter, body)


def repair_staged_extracted_context(markdown_text: str, *, content_text: str) -> str:
    frontmatter, body = loads(markdown_text)
    replacement = f"## Extracted / Captured Context\n\n{content_text.strip()}\n\n"
    repaired, count = re.subn(
        r"(?ms)^## Extracted / Captured Context\n\n.*?(?=^## |\Z)",
        replacement,
        body,
        count=1,
    )
    if count == 0:
        repaired = f"{body.rstrip()}\n\n{replacement}"
    return dumps(frontmatter, repaired)
