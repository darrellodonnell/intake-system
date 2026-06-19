from __future__ import annotations

from io import BytesIO

from pypdf import PdfReader


def extract_pdf_markdown(data: bytes) -> str:
    if not data:
        raise ValueError("PDF upload was empty.")
    try:
        reader = PdfReader(BytesIO(data))
    except Exception as exc:  # pypdf raises a mix of parser-specific exceptions.
        raise ValueError("Uploaded file could not be parsed as a PDF.") from exc

    sections = []
    for index, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
        if text:
            sections.append(f"## Page {index}\n\n{text}")

    if not sections:
        raise ValueError("No extractable text was found in the uploaded PDF.")
    return "\n\n".join(sections)
