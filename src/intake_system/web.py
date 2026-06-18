from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from intake_system.config import IntakeConfig
from intake_system.db import IntakeRepository, connect
from intake_system.frontmatter import dumps, loads
from intake_system.models import ClassifiedItem, ReviewDecision
from intake_system.review import (
    clean_final_note,
    final_relative_path,
    parse_review_decision,
    writer_for_destinations,
)


STATUS_OPTIONS = ("pending", "approved", "corrected", "skipped")
SENSITIVITY_OPTIONS = ("private", "confidential", "team_safe")


def create_app(config: IntakeConfig) -> FastAPI:
    app = FastAPI(title="Intake Review")
    app.state.config = config

    @app.get("/", response_class=HTMLResponse)
    def home() -> RedirectResponse:
        return RedirectResponse("/review", status_code=303)

    @app.get("/review", response_class=HTMLResponse)
    def review_index(item_id: int | None = None, message: str | None = None) -> HTMLResponse:
        cfg = app.state.config
        with connect(cfg.database.dsn) as conn:
            repo = IntakeRepository(conn)
            pending = repo.review_queue_items(limit=250)
            selected = _select_item(repo, pending, item_id)
        return HTMLResponse(_render_review_page(cfg, pending, selected, message=message))

    @app.post("/review/apply")
    async def apply_all(request: Request) -> RedirectResponse:
        del request
        cfg = app.state.config
        applied = 0
        with connect(cfg.database.dsn) as conn:
            repo = IntakeRepository(conn)
            for path in sorted(cfg.review.staging_root.glob("**/*.md")):
                frontmatter, _ = loads(path.read_text())
                item_id = int(frontmatter.get("intake", {}).get("item_id"))
                classified = repo.get_classified_by_id(item_id)
                if classified is None:
                    continue
                decision = _decision_from_frontmatter(frontmatter)
                if decision.status in {"approved", "corrected", "skipped"}:
                    _apply_one(cfg, repo, classified, frontmatter, decision)
                    applied += 1
            repo.commit()
        return RedirectResponse(f"/review?message=applied-{applied}", status_code=303)

    @app.post("/review/{item_id}")
    async def save_review(item_id: int, request: Request) -> RedirectResponse:
        cfg = app.state.config
        form = await _form_data(request)
        action = _first(form, "action", "save")
        with connect(cfg.database.dsn) as conn:
            repo = IntakeRepository(conn)
            classified = repo.get_classified_by_id(item_id)
            if classified is None or not classified.staged_path:
                return RedirectResponse("/review?message=missing-item", status_code=303)
            path = Path(classified.staged_path)
            frontmatter, body = loads(path.read_text())
            frontmatter = update_frontmatter_from_form(frontmatter, form)
            path.write_text(dumps(frontmatter, body))
            decision = _decision_from_frontmatter(frontmatter)
            repo.upsert_review_note(item_id, str(path), frontmatter)
            if action == "apply" and decision.status in {"approved", "corrected", "skipped"}:
                _apply_one(cfg, repo, classified, frontmatter, decision)
            repo.commit()
        return RedirectResponse(f"/review?item_id={item_id}&message=saved", status_code=303)

    @app.get("/healthz")
    def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


async def _form_data(request: Request) -> dict[str, list[str]]:
    body = await request.body()
    return parse_qs(body.decode("utf-8"), keep_blank_values=True)


def update_frontmatter_from_form(frontmatter: dict[str, Any], form: dict[str, list[str]]) -> dict[str, Any]:
    updated = dict(frontmatter)
    review = dict(updated.get("review") or {})
    actions = dict(updated.get("actions") or {})
    destinations = form.get("approved_destinations") or []
    approved_actions = _first(form, "approved_actions", "")
    review["status"] = _first(form, "status", review.get("status", "pending"))
    review["approved_destinations"] = destinations
    review["sensitivity"] = _first(form, "sensitivity", review.get("sensitivity", "private"))
    review["remember_rule"] = _first(form, "remember_rule", "") == "true"
    review["correction_note"] = _first(form, "correction_note", "").strip() or None
    actions["approved"] = [line.strip() for line in approved_actions.splitlines() if line.strip()]
    updated["review"] = review
    updated["actions"] = actions
    return updated


def _decision_from_frontmatter(frontmatter: dict[str, Any]) -> ReviewDecision:
    return parse_review_decision(dumps(frontmatter, ""))[1]


def _apply_one(
    cfg: IntakeConfig,
    repo: IntakeRepository,
    classified: ClassifiedItem,
    frontmatter: dict[str, Any],
    decision: ReviewDecision,
) -> str | None:
    final_path = None
    if decision.status in {"approved", "corrected"}:
        content = clean_final_note(classified, decision)
        writer = writer_for_destinations(cfg.destinations)
        for destination in decision.destinations:
            if destination not in cfg.destinations:
                raise ValueError(f"unknown destination {destination!r}")
            written = writer.write_text(
                destination,
                final_relative_path(classified),
                content,
                idempotency_key=f"final:{classified.record.item.source}:{classified.record.item.source_id}:{destination}",
            )
            final_path = final_path or str(written)
        if decision.status == "corrected" or decision.remember_rule:
            repo.record_corrected_example(
                classified,
                corrected_destination=decision.destinations[0],
                corrected_sensitivity=decision.sensitivity,
                correction_note=decision.correction_note,
                frontmatter=frontmatter,
            )
    repo.record_review_result(
        classified.record.id,
        status=decision.status,
        final_path=final_path,
        frontmatter=frontmatter,
    )
    return final_path


def _select_item(
    repo: IntakeRepository,
    pending: list[ClassifiedItem],
    item_id: int | None,
) -> ClassifiedItem | None:
    if item_id is not None:
        return repo.get_classified_by_id(item_id)
    return pending[0] if pending else None


def _render_review_page(
    cfg: IntakeConfig,
    pending: list[ClassifiedItem],
    selected: ClassifiedItem | None,
    *,
    message: str | None,
) -> str:
    detail = _render_empty_detail()
    if selected is not None:
        detail = _render_detail(cfg, selected)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Intake Review</title>
  <style>{_css()}</style>
</head>
<body>
  <header>
    <div>
      <h1>Intake Review</h1>
      <p>{len(pending)} pending</p>
    </div>
    <form method="post" action="/review/apply">
      <button type="submit">Apply Decisions</button>
    </form>
  </header>
  {_message(message)}
  <main>
    <nav aria-label="Pending review items">
      {_render_list(pending, selected)}
    </nav>
    <section>
      {detail}
    </section>
  </main>
</body>
</html>"""


def _render_list(pending: list[ClassifiedItem], selected: ClassifiedItem | None) -> str:
    if not pending:
        return '<div class="empty">No pending items</div>'
    selected_id = selected.record.id if selected else None
    rows = []
    for classified in pending:
        item = classified.record.item
        c = classified.classification
        active = " active" if classified.record.id == selected_id else ""
        rows.append(
            f"""<a class="item{active}" href="/review?item_id={classified.record.id}">
  <strong>{escape(item.title)}</strong>
  <span>{escape(c.primary_destination)} · {c.confidence:.2f} · {escape(c.sensitivity)}</span>
</a>"""
        )
    return "\n".join(rows)


def _render_detail(cfg: IntakeConfig, classified: ClassifiedItem) -> str:
    if not classified.staged_path:
        return _render_empty_detail()
    path = Path(classified.staged_path)
    frontmatter, body = loads(path.read_text())
    item = classified.record.item
    classification = frontmatter.get("classification") or {}
    review = frontmatter.get("review") or {}
    actions = frontmatter.get("actions") or {}
    destination_values = review.get("approved_destinations") or []
    approved_actions = "\n".join(str(value) for value in actions.get("approved") or [])
    return f"""<article>
  <div class="title-row">
    <div>
      <h2>{escape(item.title)}</h2>
      <a href="{escape(item.source_url or '#')}" target="_blank" rel="noreferrer">{escape(item.source_url or 'No source URL')}</a>
    </div>
    <div class="meta">{escape(item.source_type)} · {escape(item.source)}</div>
  </div>
  <form method="post" action="/review/{classified.record.id}">
    <div class="controls">
      <label>Status {_select('status', STATUS_OPTIONS, str(review.get('status', 'pending')))}</label>
      <label>Sensitivity {_select('sensitivity', SENSITIVITY_OPTIONS, str(review.get('sensitivity', 'private')))}</label>
      <label class="check"><input type="checkbox" name="remember_rule" value="true" {_checked(bool(review.get('remember_rule')))}> Remember rule</label>
    </div>
    <div class="destinations">
      {_destination_checkboxes(cfg, destination_values)}
    </div>
    <label>Correction note
      <textarea name="correction_note" rows="2">{escape(str(review.get('correction_note') or ''))}</textarea>
    </label>
    <label>Approved actions
      <textarea name="approved_actions" rows="3">{escape(approved_actions)}</textarea>
    </label>
    <div class="buttons">
      <button type="submit" name="action" value="save">Save</button>
      <button type="submit" name="action" value="apply">Save + Apply</button>
    </div>
  </form>
  <div class="split">
    <div>
      <h3>Recommendation</h3>
      <dl>
        <dt>Primary</dt><dd>{escape(str(classification.get('primary_destination', '')))}</dd>
        <dt>Confidence</dt><dd>{escape(str(classification.get('confidence', '')))}</dd>
        <dt>Rationale</dt><dd>{escape(str(classification.get('rationale', '')))}</dd>
      </dl>
    </div>
    <div>
      <h3>Staged Note</h3>
      <pre>{escape(body)}</pre>
    </div>
  </div>
</article>"""


def _destination_checkboxes(cfg: IntakeConfig, selected: list[str]) -> str:
    selected_set = set(selected)
    rows = []
    for key, destination in cfg.destinations.items():
        rows.append(
            f"""<label><input type="checkbox" name="approved_destinations" value="{escape(key)}" {_checked(key in selected_set)}> {escape(destination.label)}</label>"""
        )
    return "\n".join(rows)


def _select(name: str, options: tuple[str, ...], selected: str) -> str:
    opts = []
    for option in options:
        attr = " selected" if option == selected else ""
        opts.append(f'<option value="{escape(option)}"{attr}>{escape(option)}</option>')
    return f'<select name="{escape(name)}">{"".join(opts)}</select>'


def _checked(value: bool) -> str:
    return "checked" if value else ""


def _message(message: str | None) -> str:
    if not message:
        return ""
    return f'<div class="message">{escape(message)}</div>'


def _render_empty_detail() -> str:
    return '<article class="empty">No item selected</article>'


def _first(form: dict[str, list[str]], key: str, default: str = "") -> str:
    values = form.get(key)
    if not values:
        return default
    return values[0]


def _css() -> str:
    return """
:root { color-scheme: light; --ink:#20242a; --muted:#667085; --line:#d8dee8; --panel:#f7f8fa; --accent:#0f766e; --warn:#9a3412; }
* { box-sizing: border-box; }
body { margin:0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color:var(--ink); background:#fff; }
header { height:64px; display:flex; align-items:center; justify-content:space-between; padding:0 20px; border-bottom:1px solid var(--line); background:#fff; }
h1 { font-size:18px; margin:0; letter-spacing:0; }
header p { margin:3px 0 0; color:var(--muted); font-size:13px; }
button { border:1px solid #0b5f59; background:var(--accent); color:#fff; height:34px; padding:0 12px; border-radius:6px; font-weight:650; cursor:pointer; }
button[value="save"] { background:#fff; color:var(--ink); border-color:var(--line); }
main { display:grid; grid-template-columns:minmax(280px, 360px) 1fr; min-height:calc(100vh - 64px); }
nav { border-right:1px solid var(--line); background:var(--panel); overflow:auto; max-height:calc(100vh - 64px); }
.item { display:block; padding:12px 14px; border-bottom:1px solid var(--line); color:var(--ink); text-decoration:none; }
.item strong { display:block; font-size:14px; line-height:1.25; font-weight:650; }
.item span { display:block; margin-top:5px; color:var(--muted); font-size:12px; }
.item.active { background:#fff; box-shadow:inset 3px 0 0 var(--accent); }
section { overflow:auto; max-height:calc(100vh - 64px); }
article { padding:22px; }
.title-row { display:flex; align-items:flex-start; justify-content:space-between; gap:18px; padding-bottom:16px; border-bottom:1px solid var(--line); }
h2 { font-size:22px; line-height:1.2; margin:0 0 6px; letter-spacing:0; }
a { color:#175cd3; }
.meta { color:var(--muted); white-space:nowrap; font-size:13px; }
form { margin:18px 0; }
.controls { display:grid; grid-template-columns: repeat(3, minmax(160px, 1fr)); gap:12px; align-items:end; }
label { display:block; font-size:12px; color:var(--muted); font-weight:650; }
select, textarea { width:100%; margin-top:5px; border:1px solid var(--line); border-radius:6px; padding:8px; font:inherit; color:var(--ink); background:#fff; }
textarea { resize:vertical; }
.check { display:flex; align-items:center; gap:8px; color:var(--ink); height:34px; }
.check input { width:16px; height:16px; }
.destinations { display:grid; grid-template-columns: repeat(auto-fit, minmax(210px, 1fr)); gap:8px 12px; margin:14px 0; padding:12px; border:1px solid var(--line); border-radius:6px; background:#fbfcfd; }
.destinations label { color:var(--ink); font-weight:500; }
.buttons { display:flex; gap:10px; margin-top:14px; }
.split { display:grid; grid-template-columns:minmax(260px, .42fr) 1fr; gap:18px; }
h3 { font-size:14px; margin:0 0 10px; letter-spacing:0; }
dl { margin:0; border:1px solid var(--line); border-radius:6px; overflow:hidden; }
dt { background:var(--panel); color:var(--muted); font-size:12px; font-weight:700; padding:8px 10px; border-top:1px solid var(--line); }
dt:first-child { border-top:0; }
dd { margin:0; padding:10px; font-size:14px; line-height:1.45; }
pre { margin:0; padding:14px; border:1px solid var(--line); border-radius:6px; background:#0f172a; color:#e5e7eb; overflow:auto; max-height:60vh; white-space:pre-wrap; font-size:13px; line-height:1.45; }
.empty { padding:24px; color:var(--muted); }
.message { padding:8px 20px; border-bottom:1px solid #fed7aa; background:#fff7ed; color:var(--warn); font-size:13px; }
@media (max-width: 860px) {
  main { grid-template-columns:1fr; }
  nav { max-height:280px; border-right:0; border-bottom:1px solid var(--line); }
  section { max-height:none; }
  .controls, .split { grid-template-columns:1fr; }
  .title-row { display:block; }
  .meta { margin-top:8px; }
}
"""
