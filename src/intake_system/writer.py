from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from intake_system.ids import slugify


class WriterError(ValueError):
    pass


@dataclass(frozen=True)
class MarkdownWriter:
    allowed_roots: dict[str, Path]

    def write_text(self, destination: str, relative_path: str, content: str, *, idempotency_key: str) -> Path:
        if not idempotency_key:
            raise WriterError("idempotency_key is required")
        root = self._root(destination)
        target = self._safe_target(root, relative_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.read_text() == content:
            return target
        target.write_text(content)
        return target

    def _root(self, destination: str) -> Path:
        try:
            return self.allowed_roots[destination].expanduser().resolve()
        except KeyError as exc:
            raise WriterError(f"unknown destination: {destination}") from exc

    @staticmethod
    def _safe_target(root: Path, relative_path: str) -> Path:
        if Path(relative_path).is_absolute():
            raise WriterError("relative_path must not be absolute")
        target = (root / relative_path).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise WriterError(f"path escapes allowed root: {relative_path}") from exc
        if target.suffix.lower() not in {".md", ".markdown"}:
            raise WriterError("writer only creates Markdown files")
        return target


def note_filename(title: str, source_id: str) -> str:
    return f"{slugify(title)}-{slugify(source_id, max_length=24)}.md"

