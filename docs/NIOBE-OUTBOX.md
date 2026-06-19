# NIOBE Outbox

Intake is a pre-processor. It ingests messy captures, normalizes/extracts them, records Darrell's review decision, and emits durable packets for NIOBE. NIOBE decides what becomes G-brain memory, KB material, task candidates, or no-op.

## Packet Types

### `intake.reviewed_item`

Emitted after a reviewed item is approved or corrected.

Primary intent:

- give NIOBE the approved source artifact;
- preserve Darrell's review decision and correction note;
- let NIOBE choose downstream writes to G-brain, KBs, tasks, or briefings.

Key payload fields:

- `item_ref`: intake item id and source identity.
- `source`: title, URL, source type, capture metadata, parent context when available.
- `review`: approved KBs, sensitivity, correction note, remember-rule flag.
- `classification`: intake's best guess and rationale.
- `understanding`: material type, processing plan, why-saved hypothesis.
- `actions`: suggested and approved action candidates.
- `content`: extraction status and a bounded excerpt.
- `preprocessor_outputs`: staged path and transitional/legacy final Markdown path.

### `intake.clarification_needed`

Emitted when pending review items are ambiguous enough that NIOBE should batch a question for Darrell.

Initial ambiguity signals:

- low classifier confidence;
- multiple plausible knowledge bases;
- missing extracted content;
- extraction errors;
- confidential/sensitive material.

NIOBE should turn the packet into a concise question, learn from Darrell's answer, and update routing/memory behavior before downstream filing.

## Current Commands

```bash
intake outbox pending
intake outbox pending --json
intake outbox build-clarifications
intake outbox build-clarifications --apply
```

The outbox is intentionally inspectable before Hermes delivery is wired in.

## Candidate Downstream: GBrain

Darrell's deployed GBrain instance exposes:

```text
https://gbrain.quagga-chicken.ts.net/ingest
```

Observed behavior:

- `OPTIONS /ingest` returns `Allow: POST`.
- `GET /ingest` returns `404`.
- Public GBrain docs describe webhook ingestion as `POST /ingest` with `Authorization: Bearer $TOKEN` and `Content-Type: text/markdown`.

Architecture rule:

- Intake should not post directly to GBrain as part of review apply.
- NIOBE should consume `intake.reviewed_item` packets, decide what deserves memory or KB treatment, then write to GBrain through this endpoint when appropriate.
- The bearer token and endpoint should live in ignored local config or NIOBE/Hermes secrets, never in intake packet payloads or committed config.
