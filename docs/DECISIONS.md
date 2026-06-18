# Decisions

## 2026-06-17: V0 Product And Architecture Defaults

- Darrell is a product/architecture peer; use strong defaults and record decisions rather than blocking on exhaustive interviews.
- The implementation lives in `darrellodonnell/intake-system`, not `my-agentic-os`.
- The system is AOS-adjacent: it can publish/use AOS events and MCP-style boundaries, but intake-specific code and tables remain outside AOS core.
- Development services run on `minimini` with Docker; `minimini` pulls from GitHub.
- State uses the deployed Droplet PostgreSQL under a separate `intake` schema.
- V0 ingests Readwise in batch mode and supports full backfill.
- Readwise is input-only in V0; no tag/writeback side effects.
- All items are staged into a private Markdown review queue before final routing.
- Final notes are generated as clean Markdown after approval; staged review notes remain as audit records.
- Ayra/client/member/prospect/sensitive strategy material is conservative-by-default and stays private until explicitly approved.
- Learning stores explicit routing rules plus corrected examples.
- Durable state must not contain reusable credentials, bearer tokens, API keys, or broad filesystem authority.

## 2026-06-17: Tooling

- Use Python for the CLI/service and `uv` for dependency locking and Docker-friendly reproducible installs.
- Use frontmatter edits as the V0 review action surface.
- Use logical destination keys in git-managed config; absolute paths and credentials stay in ignored local config/env.

