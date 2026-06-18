from intake_system.readwise import normalize_readwise_item


def test_normalize_readwise_youtube_item() -> None:
    item = normalize_readwise_item(
        {
            "id": "abc",
            "title": "Systems Thinking for AI Teams",
            "url": "https://www.youtube.com/watch?v=123",
            "author": "Example Creator",
            "tags": {"aos": {}, "learning": {}},
            "summary": "A practical discussion.",
            "saved_at": "2026-06-17T12:00:00Z",
        }
    )

    assert item.source == "readwise"
    assert item.source_type == "youtube"
    assert item.readwise_tags == ["aos", "learning"]
    assert item.content_status == "extracted"
    assert item.review_priority == 90


def test_normalize_readwise_can_mark_fixture_source() -> None:
    item = normalize_readwise_item({"id": "sample", "title": "Fixture"}, source="fixture")

    assert item.source == "fixture"


def test_normalize_readwise_failed_extraction_still_creates_metadata_item() -> None:
    item = normalize_readwise_item({"id": "xyz", "title": "Only Metadata", "url": "https://example.com"})

    assert item.content_status == "metadata_only"
    assert item.content_text is None


def test_normalize_readwise_prefers_original_source_url_over_reader_url() -> None:
    item = normalize_readwise_item(
        {
            "id": "tweet-1",
            "title": "A frontier without an ecosystem is not stable",
            "url": "https://read.readwise.io/read/01kv3xb4jnfppd7smpnm9fejfa",
            "source_url": "https://twitter.com/satyanadella/status/2066182223213293753/?rw_tt_thread=False",
            "category": "tweet",
        }
    )

    assert item.source_url == "https://x.com/satyanadella/status/2066182223213293753/?rw_tt_thread=False"
    assert item.source_type == "x_twitter"
