import pytest

from intake_system import pdf_extract
from intake_system.pdf_extract import extract_pdf_markdown


class FakePage:
    def __init__(self, text: str | None):
        self.text = text

    def extract_text(self) -> str | None:
        return self.text


class FakeReader:
    def __init__(self, _stream):
        self.pages = [
            FakePage("First page\n\nwith text."),
            FakePage(None),
            FakePage("Third page"),
        ]


def test_extract_pdf_markdown_keeps_page_references(monkeypatch) -> None:
    monkeypatch.setattr(pdf_extract, "PdfReader", FakeReader)

    markdown = extract_pdf_markdown(b"%PDF-pretend")

    assert "## Page 1" in markdown
    assert "First page\nwith text." in markdown
    assert "## Page 2" not in markdown
    assert "## Page 3" in markdown
    assert "Third page" in markdown


def test_extract_pdf_markdown_rejects_empty_upload() -> None:
    with pytest.raises(ValueError, match="empty"):
        extract_pdf_markdown(b"")
