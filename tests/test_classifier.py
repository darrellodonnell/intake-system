from intake_system.classifier import classify_item
from intake_system.models import SourceItem


def item(title: str, *, url: str = "https://example.com", tags: list[str] | None = None) -> SourceItem:
    return SourceItem(
        source="readwise",
        source_id="1",
        source_type="article",
        title=title,
        author=None,
        source_url=url,
        captured_at=None,
        readwise_tags=tags or [],
        raw={},
        content_text=None,
    )


def test_ayra_member_signal_is_confidential() -> None:
    classification = classify_item(
        item("David Treat on AI learning"),
        active_context={"people": {"ayra_members": ["David Treat"]}},
    )

    assert classification.primary_destination == "ayra_confidential"
    assert classification.sensitivity == "confidential"
    assert classification.confidence >= 0.8


def test_travel_defaults_to_personal_knowledge_base() -> None:
    classification = classify_item(item("Living in Portugal as a remote worker"))

    assert classification.primary_destination == "personal"
    assert classification.sensitivity == "private"
