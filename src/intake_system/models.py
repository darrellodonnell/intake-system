from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class SourceItem:
    source: str
    source_id: str
    source_type: str
    title: str
    author: str | None
    source_url: str | None
    captured_at: datetime | None
    readwise_tags: list[str]
    raw: dict[str, Any]
    content_text: str | None = None
    content_status: str = "metadata_only"
    content_error: str | None = None
    review_priority: int = 50


@dataclass(frozen=True)
class Classification:
    primary_destination: str
    destination_candidates: list[str]
    confidence: float
    sensitivity: str
    rationale: str
    extracted_topics: list[str] = field(default_factory=list)
    mentioned_people: list[str] = field(default_factory=list)
    mentioned_orgs: list[str] = field(default_factory=list)
    suggested_actions: list[str] = field(default_factory=list)
    classifier_version: str = "rules-v0"


@dataclass(frozen=True)
class ItemRecord:
    id: int
    item: SourceItem


@dataclass(frozen=True)
class ClassifiedItem:
    record: ItemRecord
    classification: Classification
    staged_path: str | None = None


@dataclass(frozen=True)
class ReviewDecision:
    status: str
    destinations: list[str]
    sensitivity: str
    remember_rule: bool
    correction_note: str | None
    suggested_actions: list[str]

