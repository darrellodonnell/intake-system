CREATE SCHEMA IF NOT EXISTS intake;

CREATE TABLE IF NOT EXISTS intake.runs (
    id BIGSERIAL PRIMARY KEY,
    run_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    detail JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS intake.sources (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    last_cursor TEXT,
    last_synced_at TIMESTAMPTZ,
    state JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS intake.items (
    id BIGSERIAL PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT,
    source_url TEXT,
    captured_at TIMESTAMPTZ,
    readwise_tags JSONB NOT NULL DEFAULT '[]'::jsonb,
    raw JSONB NOT NULL DEFAULT '{}'::jsonb,
    content_text TEXT,
    content_status TEXT NOT NULL DEFAULT 'metadata_only',
    content_error TEXT,
    content_hash TEXT NOT NULL,
    review_priority INTEGER NOT NULL DEFAULT 50,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source, source_id)
);

CREATE TABLE IF NOT EXISTS intake.classifications (
    item_id BIGINT PRIMARY KEY REFERENCES intake.items(id) ON DELETE CASCADE,
    primary_destination TEXT NOT NULL,
    destination_candidates JSONB NOT NULL DEFAULT '[]'::jsonb,
    confidence NUMERIC(4,3) NOT NULL,
    sensitivity TEXT NOT NULL,
    rationale TEXT NOT NULL,
    extracted_topics JSONB NOT NULL DEFAULT '[]'::jsonb,
    mentioned_people JSONB NOT NULL DEFAULT '[]'::jsonb,
    mentioned_orgs JSONB NOT NULL DEFAULT '[]'::jsonb,
    suggested_actions JSONB NOT NULL DEFAULT '[]'::jsonb,
    classifier_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intake.review_notes (
    item_id BIGINT PRIMARY KEY REFERENCES intake.items(id) ON DELETE CASCADE,
    staged_path TEXT NOT NULL,
    final_path TEXT,
    review_status TEXT NOT NULL DEFAULT 'pending',
    frontmatter JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intake.routing_rules (
    id BIGSERIAL PRIMARY KEY,
    rule_key TEXT NOT NULL UNIQUE,
    condition TEXT NOT NULL,
    destination TEXT NOT NULL,
    rationale TEXT NOT NULL,
    confidence NUMERIC(4,3) NOT NULL DEFAULT 0.8,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS intake.corrected_examples (
    id BIGSERIAL PRIMARY KEY,
    item_id BIGINT REFERENCES intake.items(id) ON DELETE SET NULL,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    corrected_destination TEXT NOT NULL,
    corrected_sensitivity TEXT NOT NULL,
    correction_note TEXT,
    frontmatter JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_intake_items_source_type ON intake.items(source_type);
CREATE INDEX IF NOT EXISTS idx_intake_items_priority ON intake.items(review_priority DESC, captured_at DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS idx_intake_review_status ON intake.review_notes(review_status);

