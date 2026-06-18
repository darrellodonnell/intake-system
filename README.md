# Intake System

Readwise-first intake, triage, review, and Markdown routing for Darrell's personal/professional knowledge bases.

V0 is intentionally AOS-adjacent rather than AOS core:

- batch-poll Readwise and support full historical backfill;
- store state in the deployed Droplet PostgreSQL under a separate `intake` schema;
- stage every item into a private Markdown review queue;
- learn from frontmatter corrections as explicit rules plus corrected examples;
- create clean final Markdown notes only after approval;
- keep durable state free of raw credentials and broad filesystem authority.

## Quick Start

```bash
uv sync --extra dev
cp config/intake.example.yaml config/intake.local.yaml
uv run intake db migrate --config config/intake.local.yaml
uv run intake fixtures load fixtures/readwise-sample.json --config config/intake.local.yaml
uv run intake readwise sync --config config/intake.local.yaml
uv run intake classify pending --config config/intake.local.yaml
uv run intake review build-daily --config config/intake.local.yaml
```

For local fixture-only development without a database:

```bash
uv run pytest
```

## Docker

The dev container is designed to run on `minimini` and connect to the deployed Droplet PostgreSQL via `INTAKE_DATABASE_DSN`.

```bash
cp .env.example .env
docker compose -f docker-compose.dev.yml run --rm intake db migrate
docker compose -f docker-compose.dev.yml run --rm intake fixtures load fixtures/readwise-sample.json
docker compose -f docker-compose.dev.yml run --rm intake backfill run
```

Run the review UI:

```bash
docker compose -f docker-compose.dev.yml up -d intake-ui
```

Open `http://minimini:8087/review`.

## Configuration

The committed [`config/intake.example.yaml`](config/intake.example.yaml) defines logical destinations and safe defaults. Copy it to `config/intake.local.yaml` for real paths and credentials. The local file is ignored by git.

See [docs/MINIMINI-DEV.md](docs/MINIMINI-DEV.md) for the minimini + Droplet PostgreSQL runbook.
