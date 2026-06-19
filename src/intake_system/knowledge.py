from __future__ import annotations

from intake_system.models import Classification, SourceItem


KNOWLEDGE_BASE_KEYS = (
    "personal",
    "professional",
    "ayra_corporate_internal",
    "ayra_confidential",
)

KNOWLEDGE_BASE_LABELS = {
    "personal": "Personal",
    "professional": "Professional",
    "ayra_corporate_internal": "Ayra Corporate",
    "ayra_confidential": "Ayra Confidential",
}

MATERIAL_TYPES = (
    "article",
    "social post/thread",
    "video",
    "audio",
    "pdf/document",
    "personal note",
    "company/client material",
    "idea/thought",
    "reference/resource",
)

PROCESSING_INTENTS = (
    "Summarize",
    "Extract actions",
    "Extract people/orgs",
    "Extract claims/insights",
    "Extract product ideas",
    "Save as reference",
    "Connect to AOS/Ayra context",
    "Transcribe and extract for video/audio",
    "Defer/no further processing",
)

LEGACY_KNOWLEDGE_BASE_MAP = {
    "aos": "professional",
    "general_business": "professional",
    "travel": "personal",
    "continuum_loop": "personal",
    "ayra_team_safe": "ayra_corporate_internal",
    "ayra_private": "ayra_confidential",
    "client_specific": "ayra_confidential",
    "inbox": "professional",
}


def canonical_knowledge_base(key: str) -> str:
    return LEGACY_KNOWLEDGE_BASE_MAP.get(key, key)


def canonical_knowledge_bases(keys: list[str] | tuple[str, ...]) -> list[str]:
    values: list[str] = []
    for key in keys:
        canonical = canonical_knowledge_base(str(key))
        if canonical in KNOWLEDGE_BASE_KEYS and canonical not in values:
            values.append(canonical)
    return values


def default_knowledge_bases(classification: Classification) -> list[str]:
    values = canonical_knowledge_bases(classification.destination_candidates)
    if "ayra_confidential" in values and "ayra_corporate_internal" in values:
        values.remove("ayra_corporate_internal")
    return values or [canonical_knowledge_base(classification.primary_destination)]


def infer_material_type(item: SourceItem, classification: Classification) -> str:
    text = _haystack(item, classification)
    if any(term in text for term in ("client", "prospect", "member", "ayra")):
        return "company/client material"
    if item.source_type == "youtube":
        return "video"
    if item.source_type in {"x_twitter", "linkedin"}:
        return "social post/thread"
    if item.source_type in {"pdf", "document"}:
        return "pdf/document"
    if item.source_type in {"pinboard", "bookmark"}:
        return "reference/resource"
    if item.source_type in {"audio", "podcast"}:
        return "audio"
    if any(term in text for term in ("idea", "thought", "hypothesis")):
        return "idea/thought"
    if item.content_status == "metadata_only":
        return "reference/resource"
    return "article"


def infer_processing_plan(item: SourceItem, classification: Classification) -> list[str]:
    text = _haystack(item, classification)
    intents: list[str] = []
    if item.source_type in {"youtube", "audio", "podcast"}:
        intents.append("Transcribe and extract for video/audio")
    if item.source_type in {"pinboard", "bookmark"}:
        intents.append("Save as reference")
    if item.content_status != "extracted" and item.source_type not in {"youtube", "audio", "podcast"}:
        intents.append("Save as reference")
    else:
        intents.append("Summarize")
    if classification.suggested_actions or "action" in text:
        intents.append("Extract actions")
    if classification.mentioned_people or classification.mentioned_orgs:
        intents.append("Extract people/orgs")
    if any(term in text for term in ("ai", "agent", "architecture", "strategy", "systems thinking", "claim")):
        intents.append("Extract claims/insights")
    if any(term in text for term in ("product", "ayra", "aos", "member", "client")):
        intents.append("Extract product ideas")
        intents.append("Connect to AOS/Ayra context")
    return _dedupe(intents) or ["Defer/no further processing"]


def infer_why_saved(item: SourceItem, classification: Classification) -> str:
    if item.source == "pinboard" or item.source_type in {"pinboard", "bookmark"}:
        note = str((item.raw or {}).get("extended") or "").strip()
        tags = item.readwise_tags
        tag_text = ", ".join(tags)
        if note and tags:
            return f"Darrell saved this Pinboard bookmark with the note: {note} Tags suggest: {tag_text}."
        if note:
            return f"Darrell saved this Pinboard bookmark with the note: {note}"
        if tags:
            return f"Darrell saved this Pinboard bookmark as a tagged reference. Tags suggest: {tag_text}."
        return "Darrell saved this URL as a Pinboard bookmark; infer intent from the title and target URL."
    return classification.rationale


def _haystack(item: SourceItem, classification: Classification) -> str:
    values = [
        item.title,
        item.author,
        item.source_url,
        item.source_type,
        " ".join(item.readwise_tags),
        item.content_text,
        classification.rationale,
        " ".join(classification.extracted_topics),
        " ".join(classification.mentioned_people),
        " ".join(classification.mentioned_orgs),
    ]
    return "\n".join(str(value).lower() for value in values if value)


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result
