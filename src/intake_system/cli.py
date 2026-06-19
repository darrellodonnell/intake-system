from __future__ import annotations

from pathlib import Path
from typing import Optional
import json

import typer
import uvicorn

from intake_system.classifier import classify_item
from intake_system.config import ConfigError, load_active_context, load_config
from intake_system.db import IntakeRepository, apply_migrations, connect, upsert_many
from intake_system.frontmatter import dumps
from intake_system.maintenance import refresh_readwise_content, repair_readwise_source_urls
from intake_system.outbox import clarification_needed_packet, reviewed_item_packet
from intake_system.readwise import ReadwiseClient
from intake_system.readwise import normalize_readwise_item
from intake_system.review import (
    build_daily_index,
    clean_final_note,
    final_relative_path,
    parse_review_decision,
    stage_review_note,
    writer_for_destinations,
)


app = typer.Typer(help="Knowledge intake, triage, review, and Markdown routing.")
db_app = typer.Typer(help="Database commands.")
readwise_app = typer.Typer(help="Readwise ingestion commands.")
backfill_app = typer.Typer(help="Backfill orchestration commands.")
classify_app = typer.Typer(help="Classification commands.")
review_app = typer.Typer(help="Markdown review commands.")
fixtures_app = typer.Typer(help="Fixture ingestion commands.")
web_app = typer.Typer(help="Review UI commands.")
maintenance_app = typer.Typer(help="Maintenance and data repair commands.")
outbox_app = typer.Typer(help="NIOBE-facing intake packet commands.")

app.add_typer(db_app, name="db")
app.add_typer(readwise_app, name="readwise")
app.add_typer(backfill_app, name="backfill")
app.add_typer(classify_app, name="classify")
app.add_typer(review_app, name="review")
app.add_typer(fixtures_app, name="fixtures")
app.add_typer(web_app, name="web")
app.add_typer(maintenance_app, name="maintenance")
app.add_typer(outbox_app, name="outbox")


def _config_path(value: Optional[Path]) -> Path | None:
    return value


def _load(path: Optional[Path]):
    try:
        return load_config(path)
    except ConfigError as exc:
        raise typer.BadParameter(str(exc)) from exc


@db_app.command("migrate")
def db_migrate(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
) -> None:
    cfg = _load(_config_path(config))
    apply_migrations(cfg.database.dsn)
    typer.echo("Applied intake schema migrations.")


@readwise_app.command("sync")
def readwise_sync(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
    full: bool = typer.Option(False, "--full", help="Ignore stored cursor and walk all available Readwise pages."),
    max_pages: int | None = typer.Option(None, "--max-pages", help="Optional safety cap for this run."),
) -> None:
    cfg = _load(_config_path(config))
    client = ReadwiseClient(
        base_url=cfg.readwise.base_url,
        token=cfg.readwise.api_token,
        page_size=cfg.readwise.page_size,
    )
    with connect(cfg.database.dsn) as conn:
        repo = IntakeRepository(conn)
        cursor = None if full else repo.get_source_cursor("readwise")
        run_id = repo.start_run("readwise.sync", {"full": full, "max_pages": max_pages})
        count = 0
        pages = 0
        final_cursor = cursor
        try:
            for items, next_cursor in client.iter_items(cursor=cursor):
                count += upsert_many(repo, items)
                pages += 1
                final_cursor = next_cursor
                repo.upsert_source_state("readwise", last_cursor=next_cursor)
                repo.commit()
                if max_pages is not None and pages >= max_pages:
                    break
            repo.finish_run(run_id, "complete", {"items": count, "pages": pages})
            repo.commit()
        except Exception:
            repo.finish_run(run_id, "failed", {"items": count, "pages": pages, "last_cursor": final_cursor})
            repo.commit()
            raise
    typer.echo(f"Synced {count} Readwise items across {pages} page(s).")


@fixtures_app.command("load")
def fixtures_load(
    fixture: Path = typer.Argument(..., help="Path to a JSON array of Readwise-shaped fixture items."),
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
) -> None:
    cfg = _load(_config_path(config))
    raw_payload = json.loads(fixture.read_text())
    if not isinstance(raw_payload, list):
        raise typer.BadParameter("fixture must be a JSON array")
    items = [normalize_readwise_item(raw, source="fixture") for raw in raw_payload]
    with connect(cfg.database.dsn) as conn:
        repo = IntakeRepository(conn)
        run_id = repo.start_run("fixtures.load", {"fixture": str(fixture)})
        try:
            count = upsert_many(repo, items)
            repo.finish_run(run_id, "complete", {"items": count})
            repo.commit()
        except Exception:
            repo.finish_run(run_id, "failed", {"fixture": str(fixture)})
            repo.commit()
            raise
    typer.echo(f"Loaded {len(items)} fixture item(s).")


@classify_app.command("pending")
def classify_pending(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
    limit: int = typer.Option(100, "--limit", help="Maximum items to classify."),
) -> None:
    cfg = _load(_config_path(config))
    active_context = load_active_context(cfg.active_context_file)
    with connect(cfg.database.dsn) as conn:
        repo = IntakeRepository(conn)
        items = repo.pending_items(limit=limit)
        for record in items:
            repo.upsert_classification(record.id, classify_item(record.item, active_context=active_context))
        repo.commit()
    typer.echo(f"Classified {len(items)} item(s).")


@review_app.command("build-daily")
def review_build_daily(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
    limit: int | None = typer.Option(None, "--limit", help="Maximum new staged notes to create."),
) -> None:
    cfg = _load(_config_path(config))
    stage_limit = limit or cfg.review.batch_size
    with connect(cfg.database.dsn) as conn:
        repo = IntakeRepository(conn)
        classified_items = repo.classified_without_review(limit=stage_limit)
        for classified in classified_items:
            path, frontmatter = stage_review_note(cfg, classified)
            repo.upsert_review_note(classified.record.id, str(path), frontmatter)
        repo.commit()
        pending = repo.pending_review_items(limit=cfg.review.batch_size)
    index = build_daily_index(cfg, pending)
    typer.echo(f"Staged {len(classified_items)} note(s). Daily review: {index}")


@review_app.command("apply")
def review_apply(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Parse decisions without writing final notes."),
) -> None:
    cfg = _load(_config_path(config))
    writer = writer_for_destinations(cfg.destinations)
    applied = 0
    skipped = 0
    with connect(cfg.database.dsn) as conn:
        repo = IntakeRepository(conn)
        for path in sorted(cfg.review.staging_root.glob("**/*.md")):
            frontmatter, decision = parse_review_decision(path.read_text())
            item_id = int(frontmatter.get("intake", {}).get("item_id"))
            classified = repo.get_classified_by_id(item_id)
            if classified is None:
                skipped += 1
                continue
            if decision.status not in {"approved", "corrected", "skipped"}:
                continue
            final_path = None
            if decision.status in {"approved", "corrected"}:
                content = clean_final_note(classified, decision)
                for destination in decision.destinations:
                    if destination not in cfg.destinations:
                        raise typer.BadParameter(f"unknown destination {destination!r} in {path}")
                    if not dry_run:
                        written = writer.write_text(
                            destination,
                            final_relative_path(classified),
                            content,
                            idempotency_key=f"final:{classified.record.item.source}:{classified.record.item.source_id}:{destination}",
                        )
                        final_path = final_path or str(written)
                if decision.status == "corrected" or decision.remember_rule:
                    repo.record_corrected_example(
                        classified,
                        corrected_destination=decision.destinations[0],
                        corrected_sensitivity=decision.sensitivity,
                        correction_note=decision.correction_note,
                        frontmatter=frontmatter,
                    )
            if not dry_run:
                repo.record_review_result(
                    classified.record.id,
                    status=decision.status,
                    final_path=final_path,
                    frontmatter=frontmatter,
                )
                if decision.status in {"approved", "corrected"}:
                    key, payload = reviewed_item_packet(
                        classified,
                        decision,
                        frontmatter=frontmatter,
                        final_path=final_path,
                    )
                    repo.upsert_outbox_packet(
                        packet_type="intake.reviewed_item",
                        recipient="niobe",
                        idempotency_key=key,
                        payload=payload,
                        item_id=classified.record.id,
                    )
            applied += 1
        if not dry_run:
            repo.commit()
    typer.echo(f"{'Would apply' if dry_run else 'Applied'} {applied} review decision(s); skipped {skipped}.")


@backfill_app.command("run")
def backfill_run(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
    max_pages: int | None = typer.Option(None, "--max-pages", help="Optional Readwise page cap."),
    review_limit: int | None = typer.Option(None, "--review-limit", help="Maximum staged notes to create."),
) -> None:
    cfg = _load(_config_path(config))
    typer.echo("Applying migrations...")
    apply_migrations(cfg.database.dsn)
    typer.echo("Syncing Readwise...")
    readwise_sync(config=cfg.path, full=True, max_pages=max_pages)
    typer.echo("Classifying pending items...")
    classify_pending(config=cfg.path, limit=1000)
    typer.echo("Building daily review batch...")
    review_build_daily(config=cfg.path, limit=review_limit)


@web_app.command("serve")
def web_serve(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host."),
    port: int = typer.Option(8087, "--port", help="Bind port."),
) -> None:
    cfg = _load(_config_path(config))
    from intake_system.web import create_app

    uvicorn.run(create_app(cfg), host=host, port=port)


@maintenance_app.command("repair-source-urls")
def maintenance_repair_source_urls(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
    apply: bool = typer.Option(False, "--apply", help="Write repaired Readwise source URLs to DB and staged notes."),
) -> None:
    cfg = _load(_config_path(config))
    with connect(cfg.database.dsn) as conn:
        result = repair_readwise_source_urls(conn, dry_run=not apply)
        if apply:
            conn.commit()
    mode = "Repaired" if apply else "Would repair"
    typer.echo(
        f"{mode} {result.updated_items} item(s), {result.updated_review_notes} review note(s), "
        f"{result.updated_staged_files} staged file(s); scanned {result.scanned}, "
        f"missing staged files {result.missing_staged_files}."
    )


@maintenance_app.command("refresh-readwise-content")
def maintenance_refresh_readwise_content(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
    item_id: list[int] | None = typer.Option(None, "--item-id", help="Limit refresh to a specific intake item id."),
    request_delay: float = typer.Option(3.5, "--request-delay", help="Seconds to pause between Readwise item fetches."),
    apply: bool = typer.Option(False, "--apply", help="Write refreshed content to DB and staged notes."),
) -> None:
    cfg = _load(_config_path(config))
    client = ReadwiseClient(
        base_url=cfg.readwise.base_url,
        token=cfg.readwise.api_token,
        page_size=cfg.readwise.page_size,
    )
    with connect(cfg.database.dsn) as conn:
        result = refresh_readwise_content(conn, client, item_ids=item_id, request_delay=request_delay, dry_run=not apply)
        if apply:
            conn.commit()
    mode = "Refreshed" if apply else "Would refresh"
    typer.echo(
        f"{mode} {result.updated_items} item(s), {result.updated_staged_files} staged file(s); "
        f"scanned {result.scanned}, fetched {result.fetched}, no content {result.no_content}, "
        f"missing staged files {result.missing_staged_files}."
    )


@outbox_app.command("pending")
def outbox_pending(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
    limit: int = typer.Option(20, "--limit", help="Maximum packets to show."),
    recipient: str | None = typer.Option(None, "--recipient", help="Filter by recipient."),
    json_output: bool = typer.Option(False, "--json", help="Emit full packets as JSON."),
) -> None:
    cfg = _load(_config_path(config))
    with connect(cfg.database.dsn) as conn:
        repo = IntakeRepository(conn)
        packets = repo.pending_outbox_packets(limit=limit, recipient=recipient)
    if json_output:
        typer.echo(json.dumps([dict(packet) for packet in packets], default=str, indent=2))
        return
    for packet in packets:
        payload = dict(packet["payload"] or {})
        item_count = len(payload.get("items") or [])
        suffix = f" items={item_count}" if item_count else ""
        typer.echo(
            f"{packet['id']} {packet['packet_type']} -> {packet['recipient']} "
            f"item={packet['item_id']} key={packet['idempotency_key']}{suffix}"
        )


@outbox_app.command("build-clarifications")
def outbox_build_clarifications(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
    limit: int = typer.Option(25, "--limit", help="Maximum pending review items to scan."),
    apply: bool = typer.Option(False, "--apply", help="Write the clarification packet to the outbox."),
) -> None:
    cfg = _load(_config_path(config))
    with connect(cfg.database.dsn) as conn:
        repo = IntakeRepository(conn)
        items = repo.pending_review_items(limit=limit)
        key, payload = clarification_needed_packet(items)
        count = len(payload.get("items") or [])
        if apply and count:
            repo.upsert_outbox_packet(
                packet_type="intake.clarification_needed",
                recipient="niobe",
                idempotency_key=key,
                payload=payload,
            )
            repo.commit()
    mode = "Queued" if apply else "Would queue"
    typer.echo(f"{mode} clarification packet with {count} item(s).")
    if not apply:
        typer.echo(json.dumps(payload, default=str, indent=2))


@outbox_app.command("backfill-reviewed")
def outbox_backfill_reviewed(
    config: Optional[Path] = typer.Option(None, "--config", "-c", help="Path to intake config YAML."),
    limit: int = typer.Option(100, "--limit", help="Maximum reviewed items to scan."),
    apply: bool = typer.Option(False, "--apply", help="Write reviewed-item packets to the outbox."),
) -> None:
    cfg = _load(_config_path(config))
    queued = 0
    with connect(cfg.database.dsn) as conn:
        repo = IntakeRepository(conn)
        rows = conn.execute(
            """
            SELECT item_id, final_path, frontmatter
            FROM intake.review_notes
            WHERE review_status IN ('approved', 'corrected')
            ORDER BY updated_at DESC, item_id
            LIMIT %s
            """,
            (limit,),
        ).fetchall()
        for row in rows:
            classified = repo.get_classified_by_id(int(row["item_id"]))
            if classified is None:
                continue
            frontmatter = dict(row["frontmatter"] or {})
            decision = parse_review_decision(dumps(frontmatter, ""))[1]
            key, payload = reviewed_item_packet(
                classified,
                decision,
                frontmatter=frontmatter,
                final_path=row["final_path"],
            )
            if apply:
                repo.upsert_outbox_packet(
                    packet_type="intake.reviewed_item",
                    recipient="niobe",
                    idempotency_key=key,
                    payload=payload,
                    item_id=classified.record.id,
                )
            queued += 1
        if apply:
            repo.commit()
    mode = "Queued" if apply else "Would queue"
    typer.echo(f"{mode} {queued} reviewed-item packet(s).")


if __name__ == "__main__":
    app()
