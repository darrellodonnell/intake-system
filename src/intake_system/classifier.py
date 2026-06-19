from __future__ import annotations

import re
from typing import Any

from intake_system.knowledge import canonical_knowledge_bases
from intake_system.models import Classification, SourceItem


AYRA_TERMS = ("ayra", "ayra.ai")
AYRA_CONFIDENTIAL_TERMS = ("david treat", "member", "client", "prospect", "confidential")
AI_TERMS = ("ai", "agent", "agentic", "llm", "learning")
AOS_TERMS = ("aos", "ehos", "agentic os", "event bus", "mcp", "agent runtime")
TRAVEL_TERMS = ("travel", "slow travel", "living in", "move to", "retire", "country")
SYSTEMS_TERMS = ("systems thinking", "architecture", "strategy", "operating model")


def classify_item(item: SourceItem, *, active_context: dict[str, Any] | None = None) -> Classification:
    active_context = active_context or {}
    text = _haystack(item)
    mentioned_people = _mentioned_people(text, active_context)
    mentioned_orgs = _mentioned_orgs(text)
    topics = _topics(text)
    candidates: list[str] = []
    rationale: list[str] = []
    sensitivity = "private"
    confidence = 0.52

    if _contains_any(text, AYRA_CONFIDENTIAL_TERMS) or mentioned_people:
        candidates.append("ayra_confidential")
        sensitivity = "confidential"
        confidence = 0.82
        rationale.append("Ayra/client/member signal detected; conservative confidentiality policy applies.")
    elif _contains_any(text, AYRA_TERMS):
        candidates.append("ayra_corporate_internal")
        sensitivity = "private"
        confidence = 0.76
        rationale.append("Ayra corporate signal detected without client/member-specific context.")
    if _contains_any(text, AOS_TERMS):
        candidates.append("professional")
        confidence = max(confidence, 0.74)
        rationale.append("Agentic systems/AOS terminology detected.")
    if _contains_any(text, TRAVEL_TERMS):
        candidates.append("personal")
        confidence = max(confidence, 0.7)
        rationale.append("Travel or living-in-place signal detected; defaults to personal unless tied to product work.")
    if _contains_any(text, SYSTEMS_TERMS) or _contains_any(text, AI_TERMS):
        candidates.append("professional")
        confidence = max(confidence, 0.66)
        rationale.append("AI, systems thinking, or general business learning signal detected.")
    if item.source_type in {"linkedin", "x_twitter"} and "ayra_confidential" not in candidates:
        candidates.append("professional")
        confidence = max(confidence, 0.62)
        rationale.append("Professional social source with no stronger knowledge base signal.")
    if not candidates:
        candidates.append("professional")
        rationale.append("No strong knowledge base signal; defaults to professional review.")

    candidates = canonical_knowledge_bases(_dedupe(candidates))
    primary = candidates[0]
    if primary != "ayra_confidential":
        sensitivity = "private"

    actions = []
    if item.source_type == "youtube":
        actions.append("Transcribe and extract summary, claims, people/orgs, and actions.")
    if "ayra_confidential" in candidates:
        actions.append("Confirm whether this is member/client-specific or team-safe.")
    if item.content_status != "extracted":
        actions.append("Content extraction incomplete; review source manually.")

    return Classification(
        primary_destination=primary,
        destination_candidates=candidates,
        confidence=round(min(confidence, 0.95), 3),
        sensitivity=sensitivity,
        rationale=" ".join(rationale),
        extracted_topics=topics,
        mentioned_people=mentioned_people,
        mentioned_orgs=mentioned_orgs,
        suggested_actions=actions,
    )


def _haystack(item: SourceItem) -> str:
    parent = item.raw.get("_parent") if isinstance(item.raw.get("_parent"), dict) else {}
    values = [
        item.title,
        item.author,
        item.source_url,
        " ".join(item.readwise_tags),
        item.content_text,
        str(item.raw.get("site_name") or ""),
        str(parent.get("title") or ""),
        str(parent.get("source_url") or ""),
        str(parent.get("site_name") or ""),
    ]
    return "\n".join(str(value).lower() for value in values if value)


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    return any(term in text for term in terms)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _mentioned_people(text: str, active_context: dict[str, Any]) -> list[str]:
    people = active_context.get("people", {})
    names: list[str] = []
    if isinstance(people, dict):
        for values in people.values():
            if isinstance(values, list):
                names.extend(str(value) for value in values)
    return [name for name in names if name.lower() in text]


def _mentioned_orgs(text: str) -> list[str]:
    orgs = []
    for org in ("Ayra", "Continuum Loop", "AOS", "SlowTravel"):
        if org.lower() in text:
            orgs.append(org)
    return orgs


def _topics(text: str) -> list[str]:
    topics = []
    for label, pattern in {
        "AI": r"\b(ai|llm|agentic|agents?)\b",
        "systems thinking": r"systems thinking|operating model|architecture",
        "learning": r"\blearning\b",
        "travel": r"\btravel\b|living in|retire",
        "Ayra": r"\bayra\b",
    }.items():
        if re.search(pattern, text):
            topics.append(label)
    return topics
