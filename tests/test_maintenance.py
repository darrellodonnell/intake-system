from intake_system.frontmatter import dumps, loads
from intake_system.maintenance import repair_staged_markdown_text


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
