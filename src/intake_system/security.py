from __future__ import annotations

from typing import Any


SENSITIVE_KEY_PARTS = ("authorization", "bearer", "token", "api_key", "apikey", "secret", "password")
REDACTED = "[REDACTED]"


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, dict):
        redacted = {}
        for key, nested in value.items():
            key_text = str(key).lower()
            if any(part in key_text for part in SENSITIVE_KEY_PARTS):
                redacted[key] = REDACTED
            else:
                redacted[key] = redact_sensitive(nested)
        return redacted
    if isinstance(value, list):
        return [redact_sensitive(item) for item in value]
    return value

