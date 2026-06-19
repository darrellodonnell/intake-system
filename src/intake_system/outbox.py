from __future__ import annotations

from intake_system.ids import content_hash, utc_now_iso
from intake_system.knowledge import KNOWLEDGE_BASE_LABELS
from intake_system.models import ClassifiedItem, ReviewDecision
from intake_system.review import parent_source_context


NIOBE_RECIPIENT = "niobe"


def reviewed_item_packet(
    classified: ClassifiedItem,
    decision: ReviewDecision,
    *,
    frontmatter: dict,
    final_path: str | None,
) -> tuple[str, dict]:
    item = classified.record.item
    classification = classified.classification
    source = {
        "source": item.source,
        "source_id": item.source_id,
        "source_type": item.source_type,
        "title": item.title,
        "author": item.author,
        "url": item.source_url,
        "captured_at": item.captured_at.isoformat() if item.captured_at else None,
        "readwise_tags": item.readwise_tags,
        "parent": parent_source_context(item),
    }
    source = {key: value for key, value in source.items() if value not in (None, [], {})}
    payload = {
        "type": "intake.reviewed_item",
        "recipient": NIOBE_RECIPIENT,
        "created_at": utc_now_iso(),
        "item_ref": {"item_id": classified.record.id, "source": item.source, "source_id": item.source_id},
        "source": source,
        "review": {
            "status": decision.status,
            "approved_kbs": decision.destinations,
            "approved_kb_labels": [KNOWLEDGE_BASE_LABELS.get(value, value) for value in decision.destinations],
            "sensitivity": decision.sensitivity,
            "correction_note": decision.correction_note,
            "remember_rule": decision.remember_rule,
        },
        "classification": {
            "primary_destination": classification.primary_destination,
            "destination_candidates": classification.destination_candidates,
            "confidence": classification.confidence,
            "sensitivity": classification.sensitivity,
            "rationale": classification.rationale,
            "topics": classification.extracted_topics,
            "mentioned_people": classification.mentioned_people,
            "mentioned_orgs": classification.mentioned_orgs,
        },
        "understanding": frontmatter.get("understanding") or {},
        "actions": {
            "suggested": classification.suggested_actions,
            "approved": decision.suggested_actions,
        },
        "content": {
            "status": item.content_status,
            "error": item.content_error,
            "excerpt": _excerpt(item.content_text),
        },
        "preprocessor_outputs": {
            "staged_path": classified.staged_path,
            "legacy_final_path": final_path,
        },
        "niobe_instruction": (
            "Interpret this reviewed intake packet and decide whether to update G-brain, "
            "write or update KB artifacts, propose tasks, or do nothing."
        ),
    }
    idempotency_key = f"niobe:reviewed:{item.source}:{item.source_id}:{decision.status}"
    return idempotency_key, payload


def clarification_needed_packet(items: list[ClassifiedItem]) -> tuple[str, dict]:
    entries = []
    for classified in items:
        signals = ambiguity_signals(classified)
        if not signals:
            continue
        item = classified.record.item
        entries.append(
            {
                "item_id": classified.record.id,
                "title": item.title,
                "source_type": item.source_type,
                "source_url": item.source_url,
                "content_status": item.content_status,
                "content_error": item.content_error,
                "classification": {
                    "primary_destination": classified.classification.primary_destination,
                    "destination_candidates": classified.classification.destination_candidates,
                    "confidence": classified.classification.confidence,
                    "sensitivity": classified.classification.sensitivity,
                    "rationale": classified.classification.rationale,
                },
                "signals": signals,
                "evidence_excerpt": _excerpt(item.content_text, max_length=360),
            }
        )
    item_ids = [str(entry["item_id"]) for entry in entries]
    payload = {
        "type": "intake.clarification_needed",
        "recipient": NIOBE_RECIPIENT,
        "created_at": utc_now_iso(),
        "items": entries,
        "candidate_question": _candidate_question(entries),
        "niobe_instruction": (
            "NIOBE should batch these unclear intake items into a concise question for Darrell. "
            "Use his answer to update routing and memory rules before downstream filing."
        ),
    }
    idempotency_key = f"niobe:clarification:{content_hash(*item_ids)[:16]}"
    return idempotency_key, payload


def ambiguity_signals(classified: ClassifiedItem) -> list[str]:
    item = classified.record.item
    classification = classified.classification
    signals: list[str] = []
    if classification.confidence < 0.65:
        signals.append("low_confidence")
    if len(classification.destination_candidates) > 1:
        signals.append("multiple_possible_kbs")
    if item.content_status != "extracted":
        signals.append("content_not_extracted")
    if item.content_error:
        signals.append("content_extraction_error")
    if classification.sensitivity == "confidential":
        signals.append("sensitive_material")
    return signals


def _candidate_question(entries: list[dict]) -> str:
    if not entries:
        return "No unclear intake items are currently queued."
    if any("content_not_extracted" in entry["signals"] for entry in entries):
        return "Some intake items are missing extracted content. Should they be skipped, manually enriched, or filed as source-only references?"
    if any("multiple_possible_kbs" in entry["signals"] for entry in entries):
        return "Several intake items can plausibly land in multiple knowledge bases. What routing rule should NIOBE learn?"
    return "These intake items have weak routing confidence. What should NIOBE infer about where they belong?"


def _excerpt(value: str | None, *, max_length: int = 700) -> str | None:
    text = " ".join(str(value or "").split())
    if not text:
        return None
    if len(text) <= max_length:
        return text
    return f"{text[:max_length].rstrip()}..."
