# Minimini Dev Runbook

Development runtime lives on `minimini`; source is edited on the laptop and pushed to GitHub. `minimini` pulls the repo and runs Docker.

## 1. Clone Or Update

```bash
mkdir -p ~/projects
cd ~/projects
git clone https://github.com/darrellodonnell/intake-system.git
cd intake-system
```

For updates:

```bash
cd ~/projects/intake-system
git pull --ff-only
```

## 2. Local Config

```bash
cp .env.example .env
cp config/intake.example.yaml config/intake.local.yaml
```

Fill `.env`:

```bash
INTAKE_CONFIG=config/intake.local.yaml
INTAKE_DATABASE_DSN=postgresql://<user>:<password>@<droplet-postgres-host>:5432/<database>
READWISE_API_TOKEN=<readwise-token>
```

Edit `config/intake.local.yaml`:

- Keep `database.schema: intake`.
- Point review roots at private minimini paths.
- Point destination paths at the Markdown knowledge-base roots available to minimini.
- Keep staging private by default.

Do not commit `.env` or `config/intake.local.yaml`.

## 3. Build

```bash
docker compose -f docker-compose.dev.yml build
```

## 4. Smoke With Fixtures

This verifies Docker, the Droplet Postgres DSN, migrations, classification, review staging, and daily review generation without touching Readwise.

```bash
docker compose -f docker-compose.dev.yml run --rm intake db migrate
docker compose -f docker-compose.dev.yml run --rm intake fixtures load fixtures/readwise-sample.json
docker compose -f docker-compose.dev.yml run --rm intake classify pending
docker compose -f docker-compose.dev.yml run --rm intake review build-daily
```

Expected:

- tables exist under the `intake` schema in deployed Postgres;
- staged review notes appear under the configured private staging root;
- a daily review index appears under the configured daily review root.

## 5. First Real Readwise Backfill

Start with a capped run even though V0 supports full backfill:

```bash
docker compose -f docker-compose.dev.yml run --rm intake readwise sync --full --max-pages 2
docker compose -f docker-compose.dev.yml run --rm intake classify pending --limit 100
docker compose -f docker-compose.dev.yml run --rm intake review build-daily --limit 25
```

Then remove `--max-pages` once the review output looks sane.

## 6. Apply Reviewed Notes

Edit review frontmatter:

```yaml
review:
  status: approved
  approved_destinations:
    - aos
  sensitivity: private
  remember_rule: false
  correction_note:
```

Apply:

```bash
docker compose -f docker-compose.dev.yml run --rm intake review apply
```

