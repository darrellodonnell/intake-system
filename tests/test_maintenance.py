from intake_system.frontmatter import dumps, loads
from intake_system.maintenance import repair_staged_extracted_context, repair_staged_markdown_text


def test_repair_staged_markdown_text_updates_source_frontmatter_and_body() -> None:
    markdown = dumps(
        {
            "source": {
                "title": "A frontier without an ecosystem is not stable",
                "url": "https://read.readwise.io/read/01kv3xb4jnfppd7smpnm9fejfa",
            }
        },
        "# A frontier without an ecosystem is not stable\n\nSource: https://read.readwise.io/read/01kv3xb4jnfppd7smpnm9fejfa\n",
    )

    repaired = repair_staged_markdown_text(
        markdown,
        source_url="https://x.com/satyanadella/status/2066182223213293753/?rw_tt_thread=False",
        readwise_url="https://read.readwise.io/read/01kv3xb4jnfppd7smpnm9fejfa",
    )

    frontmatter, body = loads(repaired)
    assert frontmatter["source"]["url"] == "https://x.com/satyanadella/status/2066182223213293753/?rw_tt_thread=False"
    assert frontmatter["source"]["readwise_url"] == "https://read.readwise.io/read/01kv3xb4jnfppd7smpnm9fejfa"
    assert "Source: https://x.com/satyanadella/status/2066182223213293753/?rw_tt_thread=False" in body


def test_repair_staged_extracted_context_replaces_placeholder_context() -> None:
    markdown = dumps(
        {"source": {"title": "25 Claude Features"}},
        "# 25 Claude Features\n\n## Extracted / Captured Context\n\nThis tweet contains no text.\n\n## Transcript / Source Reference\n\nKeep this section.",
    )

    repaired = repair_staged_extracted_context(
        markdown,
        content_text="Claude Projects might be the most underused power feature.\n\nProject Instructions are permanent.",
    )

    _, body = loads(repaired)
    assert "This tweet contains no text." not in body
    assert "Claude Projects might be the most underused power feature." in body
    assert "## Transcript / Source Reference\n\nKeep this section." in body


def test_repair_staged_markdown_text_adds_highlight_parent_context() -> None:
    markdown = dumps(
        {
            "source": {
                "title": "https://read.readwise.io/read/highlight-1",
                "url": "https://read.readwise.io/read/highlight-1",
            }
        },
        "# https://read.readwise.io/read/highlight-1\n\nSource: https://read.readwise.io/read/highlight-1\n",
    )

    repaired = repair_staged_markdown_text(
        markdown,
        source_url="https://read.readwise.io/read/highlight-1",
        readwise_url=None,
        title="Highlight: This index will give you an idea",
        parent={
            "title": "Greece Travel Guide",
            "url": "https://www.greektravel.com/",
            "source_id": "parent-1",
        },
    )

    frontmatter, body = loads(repaired)
    assert frontmatter["source"]["title"] == "Highlight: This index will give you an idea"
    assert frontmatter["source"]["parent"]["url"] == "https://www.greektravel.com/"
    assert "# Highlight: This index will give you an idea" in body
    assert "Parent Article: https://www.greektravel.com/" in body
