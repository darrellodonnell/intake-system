from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

import yaml

from intake_system.config import DestinationConfig, IntakeConfig
from intake_system.frontmatter import dumps, loads
from intake_system.ids import slugify, utc_now_iso
from intake_system.knowledge import (
    KNOWLEDGE_BASE_LABELS,
    canonical_knowledge_base,
    canonical_knowledge_bases,
    default_knowledge_bases,
    infer_material_type,
    infer_processing_plan,
)
from intake_system.models import ClassifiedItem, ReviewDecision
from intake_system.readwise import readwise_reader_url
from intake_system.writer import MarkdownWriter, note_filename


def review_frontmatter(classified: ClassifiedItem) -> dict:
    item = classified.record.item
    classification = classified.classification
    source = {
        "title": item.title,
        "author": item.author,
        "url": item.source_url,
        "readwise_tags": item.readwise_tags,
    }
    reader_url = readwise_reader_url(item.raw)
    if reader_url and reader_url != item.source_url:
        source["readwise_url"] = reader_url
    parent = parent_source_context(item)
    if parent:
        source["parent"] = parent
    return {
        "intake": {
            "item_id": classified.record.id,
            "source": item.source,
            "source_id": item.source_id,
            "source_type": item.source_type,
            "captured_at": item.captured_at.isoformat() if item.captured_at else None,
            "created_at": utc_now_iso(),
        },
        "source": source,
        "classification": {
            "primary_destination": classification.primary_destination,
            "destination_candidates": classification.destination_candidates,
            "confidence": classification.confidence,
            "sensitivity": classification.sensitivity,
            "rationale": classification.rationale,
            "extracted_topics": classification.extracted_topics,
            "mentioned_people": classification.mentioned_people,
            "mentioned_orgs": classification.mentioned_orgs,
        },
        "understanding": {
            "material_type": infer_material_type(item, classification),
            "processing_plan": infer_processing_plan(item, classification),
            "why_saved": classification.rationale,
        },
        "review": {
            "status": "pending",
            "approved_destinations": default_knowledge_bases(classification),
            "sensitivity": classification.sensitivity,
            "remember_rule": False,
            "correction_note": None,
        },
        "actions": {
            "suggested": classification.suggested_actions,
            "approved": [],
        },
    }


def review_body(classified: ClassifiedItem) -> str:
    item = classified.record.item
    classification = classified.classification
    source_link = item.source_url or "(no source URL)"
    parent = parent_source_context(item)
    parent_line = ""
    if parent:
        parent_line = f"\nParent Article: {parent.get('url')}"
    content_note = captured_context_text(item)
    transcript_note = ""
    if item.source_type == "youtube":
        transcript_note = "\n\n## Transcript / Source Reference\n\nStore a transcript reference or source-specific notes here if needed."
    actions = "\n".join(f"- {action}" for action in classification.suggested_actions) or "- No suggested actions."
    return f"""# {item.title}

Source: {source_link}{parent_line}

## Why This Was Saved

Hypothesis: {classification.rationale}

## Knowledge Base Recommendation

- Primary: `{classification.primary_destination}`
- Candidates: {", ".join(f"`{value}`" for value in classification.destination_candidates)}
- Confidence: {classification.confidence}
- Sensitivity: `{classification.sensitivity}`

## Suggested Actions

{actions}

## Extracted / Captured Context

{content_note}{transcript_note}
"""


def captured_context_text(item) -> str:
    if item.content_text:
        return item.content_text
    if item.content_error:
        reason = item.content_error
    elif item.source_type == "pdf":
        reason = "PDF text was not extracted."
    else:
        reason = "No extracted content is available yet."
    source = item.source_url or "no source URL"
    return f"Extraction status: {reason}\n\nManual review source: {source}"


def parent_source_context(item) -> dict | None:
    raw = item.raw or {}
    parent = raw.get("_parent")
    if not isinstance(parent, dict):
        return None
    url = parent.get("source_url") or parent.get("url")
    if not url:
        return None
    return {
        "title": parent.get("site_name") or parent.get("title"),
        "url": url,
        "source_id": parent.get("id"),
    }


def stage_review_note(config: IntakeConfig, classified: ClassifiedItem) -> tuple[Path, dict]:
    item = classified.record.item
    day = (item.captured_at.date() if item.captured_at else date.today()).isoformat()
    rel_path = f"{day}/{note_filename(item.title, item.source_id)}"
    writer = MarkdownWriter({"staging": config.review.staging_root})
    frontmatter = review_frontmatter(classified)
    path = writer.write_text(
        "staging",
        rel_path,
        dumps(frontmatter, review_body(classified)),
        idempotency_key=f"stage:{item.source}:{item.source_id}",
    )
    return path, frontmatter


def build_daily_index(config: IntakeConfig, items: Iterable[ClassifiedItem], *, day: date | None = None) -> Path:
    day = day or date.today()
    rows = []
    for classified in items:
        item = classified.record.item
        staged_path = Path(classified.staged_path or "")
        try:
            link = staged_path.relative_to(config.review.daily_root.parent.parent)
        except ValueError:
            link = staged_path
        rows.append(
            f"- [{item.title}]({link}) - `{_destination_label(config, classified.classification.primary_destination)}` "
            f"({classified.classification.confidence:.2f}, {classified.classification.sensitivity})"
        )
    body = "\n".join(rows) or "_No pending review items._"
    content = f"# Intake Review {day.isoformat()}\n\n{body}\n"
    writer = MarkdownWriter({"daily": config.review.daily_root})
    return writer.write_text(
        "daily",
        f"{day.isoformat()}.md",
        content,
        idempotency_key=f"daily:{day.isoformat()}",
    )


def parse_review_decision(markdown_text: str) -> tuple[dict, ReviewDecision]:
    frontmatter, _ = loads(markdown_text)
    review = frontmatter.get("review") or {}
    actions = frontmatter.get("actions") or {}
    destinations = review.get("approved_destinations") or []
    if isinstance(destinations, str):
        destinations = [destinations]
    destinations = canonical_knowledge_bases([str(value) for value in destinations])
    decision = ReviewDecision(
        status=str(review.get("status") or "pending"),
        destinations=destinations,
        sensitivity=str(review.get("sensitivity") or "private"),
        remember_rule=bool(review.get("remember_rule", False)),
        correction_note=review.get("correction_note"),
        suggested_actions=[str(value) for value in actions.get("approved") or []],
    )
    return frontmatter, decision


def clean_final_note(classified: ClassifiedItem, decision: ReviewDecision) -> str:
    item = classified.record.item
    classification = classified.classification
    frontmatter = {
        "source": {
            "type": item.source_type,
            "url": item.source_url,
            "author": item.author,
            "captured_at": item.captured_at.isoformat() if item.captured_at else None,
            "readwise_tags": item.readwise_tags,
        },
        "intake": {
            "source": item.source,
            "source_id": item.source_id,
            "reviewed_at": utc_now_iso(),
            "sensitivity": decision.sensitivity,
            "destinations": decision.destinations,
        },
        "topics": classification.extracted_topics,
    }
    parent = parent_source_context(item)
    if parent:
        frontmatter["source"]["parent"] = parent
    actions = "\n".join(f"- {action}" for action in decision.suggested_actions) or "- None."
    body = f"""# {item.title}

## Summary

{item.content_text or "Summary unavailable. See the source link."}

## Why It Matters

{classification.rationale}

## Actions

{actions}
"""
    return dumps(frontmatter, body)


def final_relative_path(classified: ClassifiedItem) -> str:
    item = classified.record.item
    year = str((item.captured_at.date() if item.captured_at else date.today()).year)
    return f"{year}/{note_filename(item.title, item.source_id)}"


def writer_for_destinations(destinations: dict[str, DestinationConfig]) -> MarkdownWriter:
    return MarkdownWriter({key: value.path for key, value in destinations.items()})


def _destination_label(config: IntakeConfig, key: str) -> str:
    canonical = canonical_knowledge_base(key)
    return KNOWLEDGE_BASE_LABELS.get(canonical, canonical)
