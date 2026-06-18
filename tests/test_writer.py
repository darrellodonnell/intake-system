from pathlib import Path

import pytest

from intake_system.writer import MarkdownWriter, WriterError


def test_writer_rejects_path_traversal(tmp_path: Path) -> None:
    writer = MarkdownWriter({"safe": tmp_path})

    with pytest.raises(WriterError):
        writer.write_text("safe", "../escape.md", "bad", idempotency_key="x")


def test_writer_requires_markdown_suffix(tmp_path: Path) -> None:
    writer = MarkdownWriter({"safe": tmp_path})

    with pytest.raises(WriterError):
        writer.write_text("safe", "note.txt", "bad", idempotency_key="x")


def test_writer_creates_markdown_inside_allowed_root(tmp_path: Path) -> None:
    writer = MarkdownWriter({"safe": tmp_path})

    path = writer.write_text("safe", "nested/note.md", "# Hi", idempotency_key="x")

    assert path == (tmp_path / "nested/note.md").resolve()
    assert path.read_text() == "# Hi"

