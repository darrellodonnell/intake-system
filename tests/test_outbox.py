from intake_system.models import Classification, ClassifiedItem, ItemRecord, ReviewDecision, SourceItem
from intake_system.outbox import ambiguity_signals, clarification_needed_packet, reviewed_item_packet


def classified_item(
    *,
    confidence: float = 0.8,
    candidates: list[str] | None = None,
    content_status: str = "extracted",
    content_error: str | None = None,
) -> ClassifiedItem:
    source = SourceItem(
        source="readwise",
        source_id="abc",
        source_type="highlight",
        title="Highlight: useful thing",
        author=None,
        source_url="https://read.readwise.io/read/abc",
        captured_at=None,
        readwise_tags=[],
        raw={"_parent": {"id": "parent", "site_name": "Example", "source_url": "https://example.com"}},
        content_text="This is useful captured content.",
        content_status=content_status,
        content_error=content_error,
    )
    classification = Classification(
        primary_destination="professional",
        destination_candidates=candidates or ["professional"],
        confidence=confidence,
        sensitivity="private",
        rationale="Professional learning signal.",
        suggested_actions=["Extract claims."],
    )
    return ClassifiedItem(record=ItemRecord(id=42, item=source), classification=classification, staged_path="/tmp/staged.md")


def test_reviewed_item_packet_addresses_niobe_with_review_context() -> None:
    decision = ReviewDecision(
        status="approved",
        destinations=["professional"],
        sensitivity="private",
        remember_rule=False,
        correction_note="Good professional reference.",
        suggested_actions=["Extract claims."],
    )

    key, payload = reviewed_item_packet(
        classified_item(),
        decision,
        frontmatter={"understanding": {"material_type": "article"}},
        final_path="/tmp/final.md",
    )

    assert key == "niobe:reviewed:readwise:abc:approved"
    assert payload["type"] == "intake.reviewed_item"
    assert payload["recipient"] == "niobe"
    assert payload["review"]["approved_kbs"] == ["professional"]
    assert payload["source"]["parent"]["url"] == "https://example.com"
    assert payload["preprocessor_outputs"]["legacy_final_path"] == "/tmp/final.md"


def test_clarification_packet_batches_ambiguous_items() -> None:
    item = classified_item(confidence=0.5, candidates=["personal", "professional"], content_status="metadata_only")

    key, payload = clarification_needed_packet([item])

    assert key.startswith("niobe:clarification:")
    assert payload["type"] == "intake.clarification_needed"
    assert payload["items"][0]["signals"] == ["low_confidence", "multiple_possible_kbs", "content_not_extracted"]
    assert "NIOBE" in payload["niobe_instruction"]


def test_ambiguity_signals_flags_content_error() -> None:
    item = classified_item(content_status="metadata_only", content_error="No PDF text.")

    assert "content_extraction_error" in ambiguity_signals(item)
