import json
from pathlib import Path

from intake_system.readwise import normalize_readwise_item


def test_sample_fixture_normalizes_all_items() -> None:
    payload = json.loads(Path("fixtures/readwise-sample.json").read_text())
    items = [normalize_readwise_item(raw) for raw in payload]

    assert len(items) == 3
    assert {item.source_id for item in items} == {
        "sample-youtube-aos",
        "sample-linkedin-ayra",
        "sample-travel",
    }
    assert any(item.source_type == "youtube" for item in items)
    assert any(item.source_type == "linkedin" for item in items)

