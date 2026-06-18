from intake_system.readwise import normalize_readwise_item
from intake_system.security import REDACTED, redact_sensitive


def test_redact_sensitive_masks_nested_credential_keys() -> None:
    raw = {
        "title": "x",
        "nested": {"api_token": "secret", "safe": "value"},
        "items": [{"Authorization": "Bearer abc"}],
    }

    redacted = redact_sensitive(raw)

    assert redacted["nested"]["api_token"] == REDACTED
    assert redacted["nested"]["safe"] == "value"
    assert redacted["items"][0]["Authorization"] == REDACTED


def test_readwise_normalization_redacts_raw_payload() -> None:
    item = normalize_readwise_item({"id": "1", "title": "x", "api_key": "abc"})

    assert item.raw["api_key"] == REDACTED

