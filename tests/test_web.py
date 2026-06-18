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
        "actions": {"approved": []},
    }
    form = {
        "status": ["corrected"],
        "approved_destinations": ["aos", "general_business"],
        "sensitivity": ["confidential"],
        "remember_rule": ["true"],
        "correction_note": ["AOS architecture reference."],
        "approved_actions": ["Create a follow-up note.\nAdd to digest."],
    }

    updated = update_frontmatter_from_form(frontmatter, form)

    assert updated["review"]["status"] == "corrected"
    assert updated["review"]["approved_destinations"] == ["aos", "general_business"]
    assert updated["review"]["sensitivity"] == "confidential"
    assert updated["review"]["remember_rule"] is True
    assert updated["review"]["correction_note"] == "AOS architecture reference."
    assert updated["actions"]["approved"] == ["Create a follow-up note.", "Add to digest."]

