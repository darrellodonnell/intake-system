CREATE TABLE IF NOT EXISTS intake.outbox (
    id BIGSERIAL PRIMARY KEY,
    packet_type TEXT NOT NULL,
    recipient TEXT NOT NULL DEFAULT 'niobe',
    status TEXT NOT NULL DEFAULT 'pending',
    item_id BIGINT REFERENCES intake.items(id) ON DELETE SET NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    processed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_intake_outbox_status ON intake.outbox(status, created_at);
CREATE INDEX IF NOT EXISTS idx_intake_outbox_item ON intake.outbox(item_id);
