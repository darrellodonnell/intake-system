from dataclasses import replace
from pathlib import Path

from intake_system.config import ClassifierConfig, DatabaseConfig, DestinationConfig, IntakeConfig, ReadwiseConfig, ReviewConfig
from intake_system.frontmatter import dumps
from intake_system.models import Classification, ClassifiedItem, ItemRecord, SourceItem
from intake_system.review import clean_final_note, parse_review_decision, stage_review_note


def classified_item() -> ClassifiedItem:
    source = SourceItem(
        source="readwise",
        source_id="abc",
        source_type="youtube",
        title="Systems Thinking for AI",
        author="Example",
        source_url="https://youtu.be/abc",
        captured_at=None,
        readwise_tags=["ai"],
        raw={},
        content_text="Short summary.",
    )
    classification = Classification(
        primary_destination="aos",
        destination_candidates=["aos", "general_business"],
        confidence=0.8,
        sensitivity="private",
        rationale="AOS terminology detected.",
        suggested_actions=["Review transcript reference."],
    )
    return ClassifiedItem(record=ItemRecord(id=7, item=source), classification=classification)


def config(tmp_path: Path) -> IntakeConfig:
    destinations = {
        "personal": DestinationConfig("personal", "Personal", tmp_path / "personal", True),
        "professional": DestinationConfig("professional", "Professional", tmp_path / "professional", True),
        "continuum_loop": DestinationConfig("continuum_loop", "Continuum Loop", tmp_path / "continuum", True),
        "aos": DestinationConfig("aos", "AOS", tmp_path / "aos", True),
        "general_business": DestinationConfig("general_business", "General", tmp_path / "general", True),
        "travel": DestinationConfig("travel", "Travel", tmp_path / "travel", True),
        "ayra_team_safe": DestinationConfig("ayra_team_safe", "Ayra Team", tmp_path / "ayra-team", False),
        "ayra_private": DestinationConfig("ayra_private", "Ayra Private", tmp_path / "ayra-private", True),
        "ayra_corporate_internal": DestinationConfig("ayra_corporate_internal", "Ayra Corp", tmp_path / "ayra-corp", True),
        "ayra_confidential": DestinationConfig("ayra_confidential", "Ayra Confidential", tmp_path / "ayra-conf", True),
        "client_specific": DestinationConfig("client_specific", "Client", tmp_path / "client", True),
        "inbox": DestinationConfig("inbox", "Inbox", tmp_path / "inbox", True),
    }
    return IntakeConfig(
        path=tmp_path / "config.yaml",
        database=DatabaseConfig("INTAKE_DATABASE_DSN", "intake"),
        readwise=ReadwiseConfig("READWISE_API_TOKEN", "https://readwise.io/api/v3", 100),
        review=ReviewConfig(tmp_path / "review" / "staging", tmp_path / "review" / "daily", 25, "private"),
        final_default_root=tmp_path / "out",
        classifier=ClassifierConfig("rules", 0.75),
        active_context_file=tmp_path / "active.yaml",
        destinations=destinations,
    )


def test_stage_review_note_contains_review_frontmatter(tmp_path: Path) -> None:
    path, frontmatter = stage_review_note(config(tmp_path), classified_item())

    assert path.exists()
    assert frontmatter["review"]["status"] == "pending"
    assert frontmatter["classification"]["destination_candidates"] == ["aos", "general_business"]
    assert "Transcript / Source Reference" in path.read_text()


def test_parse_review_decision_accepts_frontmatter_edits() -> None:
    frontmatter = {
        "intake": {"item_id": 7},
        "review": {
            "status": "corrected",
            "approved_destinations": ["general_business"],
            "sensitivity": "private",
            "remember_rule": True,
            "correction_note": "This is broad learning.",
        },
        "actions": {"approved": ["Make a task candidate."]},
    }

    _, decision = parse_review_decision(dumps(frontmatter, "# Body"))

    assert decision.status == "corrected"
    assert decision.destinations == ["general_business"]
    assert decision.remember_rule is True


def test_clean_final_note_omits_review_metadata() -> None:
    _, decision = parse_review_decision(
        dumps(
            {
                "intake": {"item_id": 7},
                "review": {"status": "approved", "approved_destinations": ["aos"], "sensitivity": "private"},
                "actions": {"approved": []},
            },
            "# Body",
        )
    )

    note = clean_final_note(classified_item(), decision)

    assert "review:" not in note
    assert "Short summary." in note

