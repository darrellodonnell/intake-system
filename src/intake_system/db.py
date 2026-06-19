from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

import psycopg
from psycopg.rows import dict_row

from intake_system.ids import content_hash, utc_now_iso
from intake_system.models import Classification, ClassifiedItem, ItemRecord, SourceItem


MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def connect(dsn: str) -> psycopg.Connection:
    return psycopg.connect(dsn, row_factory=dict_row)


def apply_migrations(dsn: str) -> None:
    migrations = _migration_files()
    if not migrations:
        raise RuntimeError("no migration files found")
    with connect(dsn) as conn:
        for migration in migrations:
            conn.execute(migration.read_text())
        conn.commit()


def _migration_files() -> list[Path]:
    candidates = [
        MIGRATIONS_DIR,
        Path.cwd() / "migrations",
        Path("/app/migrations"),
    ]
    for directory in candidates:
        migrations = sorted(directory.glob("*.sql"))
        if migrations:
            return migrations
    return []


class IntakeRepository:
    def __init__(self, conn: psycopg.Connection):
        self.conn = conn

    def start_run(self, run_type: str, detail: dict | None = None) -> int:
        row = self.conn.execute(
            "INSERT INTO intake.runs (run_type, status, detail) VALUES (%s, %s, %s) RETURNING id",
            (run_type, "running", json.dumps(detail or {})),
        ).fetchone()
        return int(row["id"])

    def finish_run(self, run_id: int, status: str, detail: dict | None = None) -> None:
        self.conn.execute(
            """
            UPDATE intake.runs
            SET status = %s, finished_at = now(), detail = detail || %s::jsonb
            WHERE id = %s
            """,
            (status, json.dumps(detail or {}), run_id),
        )

    def upsert_source_state(self, name: str, *, last_cursor: str | None) -> None:
        self.conn.execute(
            """
            INSERT INTO intake.sources (name, last_cursor, last_synced_at)
            VALUES (%s, %s, now())
            ON CONFLICT (name) DO UPDATE
            SET last_cursor = EXCLUDED.last_cursor,
                last_synced_at = EXCLUDED.last_synced_at
            """,
            (name, last_cursor),
        )

    def get_source_cursor(self, name: str) -> str | None:
        row = self.conn.execute("SELECT last_cursor FROM intake.sources WHERE name = %s", (name,)).fetchone()
        return None if row is None else row["last_cursor"]

    def upsert_item(self, item: SourceItem) -> int:
        row = self.conn.execute(
            """
            INSERT INTO intake.items (
                source, source_id, source_type, title, author, source_url, captured_at,
                readwise_tags, raw, content_text, content_status, content_error,
                content_hash, review_priority
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s, %s)
            ON CONFLICT (source, source_id) DO UPDATE
            SET source_type = EXCLUDED.source_type,
                title = EXCLUDED.title,
                author = EXCLUDED.author,
                source_url = EXCLUDED.source_url,
                captured_at = EXCLUDED.captured_at,
                readwise_tags = EXCLUDED.readwise_tags,
                raw = EXCLUDED.raw,
                content_text = EXCLUDED.content_text,
                content_status = EXCLUDED.content_status,
                content_error = EXCLUDED.content_error,
                content_hash = EXCLUDED.content_hash,
                review_priority = EXCLUDED.review_priority,
                updated_at = now()
            RETURNING id
            """,
            (
                item.source,
                item.source_id,
                item.source_type,
                item.title,
                item.author,
                item.source_url,
                item.captured_at,
                json.dumps(item.readwise_tags),
                json.dumps(item.raw),
                item.content_text,
                item.content_status,
                item.content_error,
                content_hash(item.title, item.source_url, item.content_text, item.raw),
                item.review_priority,
            ),
        ).fetchone()
        return int(row["id"])

    def pending_items(self, *, limit: int = 100) -> list[ItemRecord]:
        rows = self.conn.execute(
            """
            SELECT i.*
            FROM intake.items i
            LEFT JOIN intake.classifications c ON c.item_id = i.id
            WHERE c.item_id IS NULL
              AND i.source <> 'fixture'
            ORDER BY i.review_priority DESC, i.captured_at DESC NULLS LAST, i.id
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        return [_item_record(row) for row in rows]

    def upsert_classification(self, item_id: int, classification: Classification) -> None:
        self.conn.execute(
            """
            INSERT INTO intake.classifications (
                item_id, primary_destination, destination_candidates, confidence,
                sensitivity, rationale, extracted_topics, mentioned_people,
                mentioned_orgs, suggested_actions, classifier_version
            )
            VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s::jsonb, %s)
            ON CONFLICT (item_id) DO UPDATE
            SET primary_destination = EXCLUDED.primary_destination,
                destination_candidates = EXCLUDED.destination_candidates,
                confidence = EXCLUDED.confidence,
                sensitivity = EXCLUDED.sensitivity,
                rationale = EXCLUDED.rationale,
                extracted_topics = EXCLUDED.extracted_topics,
                mentioned_people = EXCLUDED.mentioned_people,
                mentioned_orgs = EXCLUDED.mentioned_orgs,
                suggested_actions = EXCLUDED.suggested_actions,
                classifier_version = EXCLUDED.classifier_version,
                updated_at = now()
            """,
            (
                item_id,
                classification.primary_destination,
                json.dumps(classification.destination_candidates),
                classification.confidence,
                classification.sensitivity,
                classification.rationale,
                json.dumps(classification.extracted_topics),
                json.dumps(classification.mentioned_people),
                json.dumps(classification.mentioned_orgs),
                json.dumps(classification.suggested_actions),
                classification.classifier_version,
            ),
        )

    def replace_item_content(
        self,
        item_id: int,
        content_text: str,
        *,
        provenance: str,
        provenance_detail: dict | None = None,
    ) -> ItemRecord | None:
        current = self.conn.execute("SELECT * FROM intake.items WHERE id = %s", (item_id,)).fetchone()
        if current is None:
            return None
        raw = dict(current["raw"] or {})
        content_provenance = {
            "source": provenance,
            "loaded_at": utc_now_iso(),
        }
        if provenance_detail:
            content_provenance.update(provenance_detail)
        raw["content_provenance"] = content_provenance
        row = self.conn.execute(
            """
            UPDATE intake.items
            SET raw = %s::jsonb,
                content_text = %s,
                content_status = 'extracted',
                content_error = NULL,
                content_hash = %s,
                updated_at = now()
            WHERE id = %s
            RETURNING *
            """,
            (
                json.dumps(raw),
                content_text,
                content_hash(current["title"], current["source_url"], content_text, raw),
                item_id,
            ),
        ).fetchone()
        return None if row is None else _item_record(row)

    def classified_without_review(self, *, limit: int = 100) -> list[ClassifiedItem]:
        rows = self.conn.execute(
            """
            SELECT i.*, c.primary_destination, c.destination_candidates, c.confidence,
                   c.sensitivity, c.rationale, c.extracted_topics, c.mentioned_people,
                   c.mentioned_orgs, c.suggested_actions, c.classifier_version
            FROM intake.items i
            JOIN intake.classifications c ON c.item_id = i.id
            LEFT JOIN intake.review_notes r ON r.item_id = i.id
            WHERE r.item_id IS NULL
              AND i.source <> 'fixture'
            ORDER BY i.review_priority DESC, i.captured_at DESC NULLS LAST, i.id
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        return [_classified_item(row) for row in rows]

    def pending_review_items(self, *, limit: int = 100) -> list[ClassifiedItem]:
        rows = self.conn.execute(
            """
            SELECT i.*, c.primary_destination, c.destination_candidates, c.confidence,
                   c.sensitivity, c.rationale, c.extracted_topics, c.mentioned_people,
                   c.mentioned_orgs, c.suggested_actions, c.classifier_version,
                   r.staged_path
            FROM intake.items i
            JOIN intake.classifications c ON c.item_id = i.id
            JOIN intake.review_notes r ON r.item_id = i.id
            WHERE r.review_status = 'pending'
              AND i.source <> 'fixture'
            ORDER BY i.review_priority DESC, i.captured_at DESC NULLS LAST, i.id
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        return [_classified_item(row) for row in rows]

    def review_queue_items(self, *, limit: int = 250) -> list[ClassifiedItem]:
        rows = self.conn.execute(
            """
            SELECT i.*, c.primary_destination, c.destination_candidates, c.confidence,
                   c.sensitivity, c.rationale, c.extracted_topics, c.mentioned_people,
                   c.mentioned_orgs, c.suggested_actions, c.classifier_version,
                   r.staged_path
            FROM intake.items i
            JOIN intake.classifications c ON c.item_id = i.id
            JOIN intake.review_notes r ON r.item_id = i.id
            WHERE (
                r.review_status = 'pending'
                OR (r.review_status IN ('approved', 'corrected') AND r.final_path IS NULL)
              )
              AND i.source <> 'fixture'
            ORDER BY i.review_priority DESC, i.captured_at DESC NULLS LAST, i.id
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        return [_classified_item(row) for row in rows]

    def upsert_review_note(self, item_id: int, staged_path: str, frontmatter: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO intake.review_notes (item_id, staged_path, review_status, frontmatter)
            VALUES (%s, %s, %s, %s::jsonb)
            ON CONFLICT (item_id) DO UPDATE
            SET staged_path = EXCLUDED.staged_path,
                frontmatter = EXCLUDED.frontmatter,
                updated_at = now()
            """,
            (item_id, staged_path, frontmatter.get("review", {}).get("status", "pending"), json.dumps(frontmatter)),
        )

    def record_review_result(
        self,
        item_id: int,
        *,
        status: str,
        final_path: str | None,
        frontmatter: dict,
    ) -> None:
        self.conn.execute(
            """
            UPDATE intake.review_notes
            SET review_status = %s, final_path = %s, frontmatter = %s::jsonb, updated_at = now()
            WHERE item_id = %s
            """,
            (status, final_path, json.dumps(frontmatter), item_id),
        )

    def upsert_outbox_packet(
        self,
        *,
        packet_type: str,
        recipient: str,
        idempotency_key: str,
        payload: dict,
        item_id: int | None = None,
    ) -> int:
        row = self.conn.execute(
            """
            INSERT INTO intake.outbox (
                packet_type, recipient, item_id, idempotency_key, payload
            )
            VALUES (%s, %s, %s, %s, %s::jsonb)
            ON CONFLICT (idempotency_key) DO UPDATE
            SET packet_type = EXCLUDED.packet_type,
                recipient = EXCLUDED.recipient,
                item_id = EXCLUDED.item_id,
                payload = EXCLUDED.payload,
                updated_at = now()
            RETURNING id
            """,
            (packet_type, recipient, item_id, idempotency_key, json.dumps(payload)),
        ).fetchone()
        return int(row["id"])

    def pending_outbox_packets(self, *, limit: int = 50, recipient: str | None = None) -> list[dict]:
        params: list[object] = []
        recipient_filter = ""
        if recipient:
            recipient_filter = "AND recipient = %s"
            params.append(recipient)
        params.append(limit)
        return self.conn.execute(
            f"""
            SELECT id, packet_type, recipient, status, item_id, idempotency_key,
                   payload, created_at, updated_at
            FROM intake.outbox
            WHERE status = 'pending'
              {recipient_filter}
            ORDER BY created_at, id
            LIMIT %s
            """,
            params,
        ).fetchall()

    def record_corrected_example(
        self,
        classified: ClassifiedItem,
        *,
        corrected_destination: str,
        corrected_sensitivity: str,
        correction_note: str | None,
        frontmatter: dict,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO intake.corrected_examples (
                item_id, source, source_id, corrected_destination,
                corrected_sensitivity, correction_note, frontmatter
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb)
            """,
            (
                classified.record.id,
                classified.record.item.source,
                classified.record.item.source_id,
                corrected_destination,
                corrected_sensitivity,
                correction_note,
                json.dumps(frontmatter),
            ),
        )

    def get_classified_by_id(self, item_id: int) -> ClassifiedItem | None:
        row = self.conn.execute(
            """
            SELECT i.*, c.primary_destination, c.destination_candidates, c.confidence,
                   c.sensitivity, c.rationale, c.extracted_topics, c.mentioned_people,
                   c.mentioned_orgs, c.suggested_actions, c.classifier_version,
                   r.staged_path
            FROM intake.items i
            JOIN intake.classifications c ON c.item_id = i.id
            LEFT JOIN intake.review_notes r ON r.item_id = i.id
            WHERE i.id = %s
            """,
            (item_id,),
        ).fetchone()
        return None if row is None else _classified_item(row)

    def commit(self) -> None:
        self.conn.commit()


def upsert_many(repo: IntakeRepository, items: Iterable[SourceItem]) -> int:
    count = 0
    for item in items:
        repo.upsert_item(item)
        count += 1
    return count


def _json_list(value: object) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return list(value)


def _item_record(row: dict) -> ItemRecord:
    item = SourceItem(
        source=row["source"],
        source_id=row["source_id"],
        source_type=row["source_type"],
        title=row["title"],
        author=row["author"],
        source_url=row["source_url"],
        captured_at=row["captured_at"],
        readwise_tags=_json_list(row["readwise_tags"]),
        raw=dict(row["raw"] or {}),
        content_text=row["content_text"],
        content_status=row["content_status"],
        content_error=row["content_error"],
        review_priority=row["review_priority"],
    )
    return ItemRecord(id=int(row["id"]), item=item)


def _classified_item(row: dict) -> ClassifiedItem:
    record = _item_record(row)
    classification = Classification(
        primary_destination=row["primary_destination"],
        destination_candidates=_json_list(row["destination_candidates"]),
        confidence=float(row["confidence"]),
        sensitivity=row["sensitivity"],
        rationale=row["rationale"],
        extracted_topics=_json_list(row["extracted_topics"]),
        mentioned_people=_json_list(row["mentioned_people"]),
        mentioned_orgs=_json_list(row["mentioned_orgs"]),
        suggested_actions=_json_list(row["suggested_actions"]),
        classifier_version=row["classifier_version"],
    )
    return ClassifiedItem(record=record, classification=classification, staged_path=row.get("staged_path"))
