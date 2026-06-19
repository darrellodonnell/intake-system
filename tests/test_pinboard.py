from intake_system.pinboard import PinboardClient, normalize_pinboard_bookmark


def test_normalize_pinboard_bookmark_uses_target_url_and_tags() -> None:
    item = normalize_pinboard_bookmark(
        {
            "href": "https://example.com/article",
            "description": "Useful article",
            "extended": "Why I saved this.",
            "tags": "ai reference",
            "time": "2026-06-19T12:00:00Z",
            "hash": "abc123",
        }
    )

    assert item.source == "pinboard"
    assert item.source_id == "abc123"
    assert item.source_type == "bookmark"
    assert item.title == "Useful article"
    assert item.source_url == "https://example.com/article"
    assert item.readwise_tags == ["ai", "reference"]
    assert "Pinboard note: Why I saved this." in item.content_text
    assert "Pinboard tags: ai, reference" in item.content_text


def test_pinboard_client_fetches_bookmarks_since_cursor(monkeypatch) -> None:
    calls = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self):
            return [{"href": "https://example.com", "description": "Example", "hash": "hash1"}]

    class FakeHttpClient:
        def __init__(self, *, timeout: float):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, *, params):
            calls.append((url, params))
            return FakeResponse()

    monkeypatch.setattr("intake_system.pinboard.httpx.Client", FakeHttpClient)

    items = PinboardClient(base_url="https://api.pinboard.in/v1", token="user:token").bookmarks(
        since="2026-06-01T00:00:00+00:00"
    )

    assert items[0].source == "pinboard"
    assert calls == [
        (
            "https://api.pinboard.in/v1/posts/all",
            {
                "auth_token": "user:token",
                "format": "json",
                "meta": "yes",
                "fromdt": "2026-06-01T00:00:00+00:00",
            },
        )
    ]
