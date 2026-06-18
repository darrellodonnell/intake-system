from intake_system.web import update_frontmatter_from_form


def test_update_frontmatter_from_form_sets_review_decision() -> None:
    frontmatter = {
        "review": {
            "status": "pending",
            "approved_destinations": ["inbox"],
            "sensitivity": "private",
            "remember_rule": False,
            "correction_note": None,
        },
        "understanding": {},
        "actions": {"approved": []},
    }
    form = {
        "status": ["corrected"],
        "approved_destinations": ["aos", "general_business"],
        "sensitivity": ["confidential"],
        "remember_rule": ["true"],
        "correction_note": ["AOS architecture reference."],
        "material_type": ["article"],
        "processing_plan": ["Summarize\nExtract claims/insights"],
        "why_saved": ["AOS architecture signal."],
        "approved_actions": ["Create a follow-up note.\nAdd to digest."],
    }

    updated = update_frontmatter_from_form(frontmatter, form)

    assert updated["review"]["status"] == "corrected"
    assert updated["review"]["approved_destinations"] == ["professional"]
    assert updated["review"]["sensitivity"] == "confidential"
    assert updated["review"]["remember_rule"] is True
    assert updated["review"]["correction_note"] == "AOS architecture reference."
    assert updated["understanding"]["material_type"] == "article"
    assert updated["understanding"]["processing_plan"] == ["Summarize", "Extract claims/insights"]
    assert updated["understanding"]["why_saved"] == "AOS architecture signal."
    assert updated["actions"]["approved"] == ["Create a follow-up note.", "Add to digest."]


def test_update_frontmatter_from_quick_decision_preserves_thinking() -> None:
    frontmatter = {
        "review": {
            "status": "pending",
            "approved_destinations": ["aos"],
            "sensitivity": "private",
            "remember_rule": False,
            "correction_note": None,
        },
        "actions": {"approved": ["old action"]},
    }
    form = {
        "status": ["approved"],
        "approved_destinations": ["aos"],
        "sensitivity": ["private"],
        "correction_note": ["This is correct because it informs the AOS intake architecture."],
    }

    updated = update_frontmatter_from_form(frontmatter, form)

    assert updated["review"]["status"] == "approved"
    assert updated["review"]["correction_note"] == "This is correct because it informs the AOS intake architecture."
    assert updated["actions"]["approved"] == []
