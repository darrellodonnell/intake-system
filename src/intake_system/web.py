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
from intake_system.knowledge import (
    KNOWLEDGE_BASE_KEYS,
    KNOWLEDGE_BASE_LABELS,
    MATERIAL_TYPES,
    canonical_knowledge_base,
    canonical_knowledge_bases,
    infer_material_type,
    infer_processing_plan,
)
from intake_system.models import ClassifiedItem, ReviewDecision
from intake_system.readwise import readwise_reader_url
from intake_system.review import (
    clean_final_note,
    final_relative_path,
    parse_review_decision,
    writer_for_destinations,
)


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
    understanding = dict(updated.get("understanding") or {})
    destinations = canonical_knowledge_bases(form.get("approved_destinations") or [])
    approved_actions = _first(form, "approved_actions", "")
    processing_plan = _first(form, "processing_plan", "")
    review["status"] = _first(form, "status", review.get("status", "pending"))
    review["approved_destinations"] = destinations
    review["sensitivity"] = _first(form, "sensitivity", review.get("sensitivity", "private"))
    review["remember_rule"] = _first(form, "remember_rule", "") == "true"
    review["correction_note"] = _first(form, "correction_note", "").strip() or None
    understanding["material_type"] = _first(form, "material_type", understanding.get("material_type", "")).strip()
    understanding["processing_plan"] = [line.strip() for line in processing_plan.splitlines() if line.strip()]
    understanding["why_saved"] = _first(form, "why_saved", understanding.get("why_saved", "")).strip()
    actions["approved"] = [line.strip() for line in approved_actions.splitlines() if line.strip()]
    updated["review"] = review
    updated["understanding"] = understanding
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
      <h1><a href="/review">Intake Review</a></h1>
      <p>{len(pending)} pending</p>
    </div>
    <form method="post" action="/review/apply">
      <button type="submit">Apply Decisions</button>
    </form>
  </header>
  {_message(message)}
  <main>
    <nav aria-label="Pending review items">
      {_render_list(cfg, pending, selected)}
    </nav>
    <section>
      {detail}
    </section>
  </main>
</body>
</html>"""


def _render_list(cfg: IntakeConfig, pending: list[ClassifiedItem], selected: ClassifiedItem | None) -> str:
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
  <span>{escape(_destination_label(cfg, c.primary_destination))} · {c.confidence:.2f} · {escape(c.sensitivity)}</span>
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
    understanding = _understanding(frontmatter, classified)
    primary_destination = canonical_knowledge_base(
        str(classification.get("primary_destination") or classified.classification.primary_destination)
    )
    destination_values = _selected_destination_values(review, primary_destination)
    approved_actions = "\n".join(str(value) for value in actions.get("approved") or [])
    processing_plan = "\n".join(str(value) for value in understanding.get("processing_plan") or [])
    sensitivity = str(classification.get("sensitivity") or classified.classification.sensitivity)
    source_url = item.source_url or ""
    reader_url = readwise_reader_url(item.raw)
    source_preview = _source_frame(source_url)
    return f"""<article>
  <form method="post" action="/review/{classified.record.id}" class="decision-form">
    <input type="hidden" name="action" value="apply">
    <div class="review-top">
      <div class="title-row">
        <div>
          <h2>{escape(item.title)}</h2>
          {_source_details(item, source_url, reader_url)}
        </div>
      </div>
      <section class="kb-decision">
        <h3>Where Does This Belong?</h3>
        <p class="decision-question">{escape(_decision_question(classified))}</p>
        <div class="recommendation">
          <span>Recommended knowledge base</span>
          <strong>{escape(_destination_label(cfg, primary_destination))}</strong>
          <small>{escape(str(classification.get('confidence', '')))} confidence · {escape(sensitivity)}</small>
        </div>
        <fieldset class="destination-picker">
          <legend>Knowledge bases</legend>
          {_destination_checkboxes(cfg, destination_values, primary_destination)}
        </fieldset>
      </section>
    </div>
    <div class="review-layout">
      <div class="reader">
      <div class="article-card">
        <div class="article-header">
          <h3>Saved Item</h3>
        </div>
        <div class="article-body">
          {_render_article_body(body, omitted_sections={"Why This Was Saved", "Routing Recommendation", "Knowledge Base Recommendation"})}
        </div>
      </div>
      </div>
      <aside class="decision">
        {_suggested_actions(classified.classification.suggested_actions, approved_actions)}
        {_thinking_box(review)}
        <details class="system-details">
          <summary>System thinks</summary>
          <label>What is it?
            {_select('material_type', MATERIAL_TYPES, str(understanding.get('material_type') or 'article'))}
          </label>
          <label>How should it be processed?
            <textarea name="processing_plan" rows="4">{escape(processing_plan)}</textarea>
          </label>
          <label>Why saved?
            <textarea name="why_saved" rows="3">{escape(str(understanding.get('why_saved') or ''))}</textarea>
          </label>
        </details>
        <details class="system-details">
          <summary>More options</summary>
          <div class="controls">
            <label>Sensitivity {_select('sensitivity', SENSITIVITY_OPTIONS, str(review.get('sensitivity', sensitivity)))}</label>
            <label class="check"><input type="checkbox" name="remember_rule" value="true" {_checked(bool(review.get('remember_rule')))}> Remember this decision</label>
          </div>
        </details>
        <div class="buttons decision-buttons">
          <button type="submit" name="status" value="approved">File This</button>
          <button type="submit" name="status" value="skipped" class="secondary">Skip Item</button>
        </div>
      </aside>
    </div>
  </form>
  {_lower_source_preview(source_preview)}
</article>"""


def _decision_question(classified: ClassifiedItem) -> str:
    classification = classified.classification
    if classification.sensitivity == "confidential":
        return "This looks sensitive. Should it enter a private/confidential knowledge base?"
    if classification.confidence < 0.6:
        return "I do not have enough signal. Which knowledge base should receive it?"
    if len(classification.destination_candidates) > 1:
        return "Should it enter the recommended knowledge base, or another base as well?"
    return "Should it enter the recommended knowledge base?"


def _source_details(item, source_url: str, reader_url: str | None) -> str:
    rows = [
        f"<dt>Original source</dt><dd>{_source_link(source_url) if source_url else 'No source URL'}</dd>",
    ]
    if reader_url and reader_url != source_url:
        rows.append(f"<dt>Readwise</dt><dd>{_source_link(reader_url)}</dd>")
    rows.append(f"<dt>Source type</dt><dd>{escape(item.source_type)}</dd>")
    rows.append(f"<dt>Ingested from</dt><dd>{escape(item.source)}</dd>")
    raw = item.raw or {}
    if raw.get("word_count"):
        rows.append(f"<dt>Word count</dt><dd>{escape(str(raw.get('word_count')))}</dd>")
    if raw.get("reading_time"):
        rows.append(f"<dt>Reading time</dt><dd>{escape(str(raw.get('reading_time')))}</dd>")
    if raw.get("saved_at"):
        rows.append(f"<dt>Saved in Readwise</dt><dd>{escape(str(raw.get('saved_at')))}</dd>")
    return f"""<details class="source-details">
  <summary>Source details</summary>
  <dl>{"".join(rows)}</dl>
</details>"""


def _source_link(source_url: str, label: str | None = None) -> str:
    if not source_url:
        return '<span class="source-missing">No source URL</span>'
    text = f"{label}: {source_url}" if label else source_url
    return f'<a href="{escape(source_url)}" target="_blank" rel="noreferrer">{escape(text)}</a>'


def _source_link_button(source_url: str) -> str:
    if not source_url:
        return ""
    return f'<a class="open-source" href="{escape(source_url)}" target="_blank" rel="noreferrer">Open Source</a>'


def _suggested_actions(suggested_actions: list[str], approved_actions: str) -> str:
    actions = suggested_actions or ["No suggested actions."]
    items = "".join(f"<li>{escape(action)}</li>" for action in actions)
    return f"""<section class="suggested-actions">
  <h3>Suggested Actions</h3>
  <ul>{items}</ul>
  <label>Approved actions
    <textarea name="approved_actions" rows="4">{escape(approved_actions)}</textarea>
  </label>
</section>"""


def _source_frame(source_url: str) -> str:
    if not source_url:
        return ""
    return f"""<div class="source-preview">
  <div class="article-header">
    <h3>Source Preview</h3>
    <span>Some sites block embedded previews; use Open Source if this pane is blank.</span>
  </div>
  <iframe src="{escape(source_url)}" loading="lazy" referrerpolicy="no-referrer"></iframe>
</div>"""


def _lower_source_preview(source_preview: str) -> str:
    if not source_preview:
        return ""
    return f'<div class="source-lower">{source_preview}</div>'


def _render_article_body(body: str, *, omitted_sections: set[str] | None = None) -> str:
    lines = _without_sections(body.splitlines(), omitted_sections or set())
    html: list[str] = []
    in_list = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if in_list:
                html.append("</ul>")
                in_list = False
            continue
        if line.startswith("#"):
            if in_list:
                html.append("</ul>")
                in_list = False
            level = min(len(line) - len(line.lstrip("#")), 4)
            text = line.lstrip("#").strip()
            html.append(f"<h{level}>{escape(text)}</h{level}>")
            continue
        if line.startswith("- "):
            if not in_list:
                html.append("<ul>")
                in_list = True
            html.append(f"<li>{escape(line[2:].strip())}</li>")
            continue
        if in_list:
            html.append("</ul>")
            in_list = False
        html.append(f"<p>{escape(line)}</p>")
    if in_list:
        html.append("</ul>")
    return "\n".join(html)


def _without_sections(lines: list[str], section_titles: set[str]) -> list[str]:
    if not section_titles:
        return lines
    kept: list[str] = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## "):
            title = stripped[3:].strip()
            skipping = title in section_titles
            if skipping:
                continue
        if not skipping:
            kept.append(line)
    return kept


def _thinking_box(review: dict[str, Any]) -> str:
    value = str(review.get("correction_note") or "")
    return f"""<label class="thinking">My thinking
  <textarea name="correction_note" rows="3" placeholder="Optional: why this is right, wrong, sensitive, or worth remembering.">{escape(value)}</textarea>
</label>"""


def _selected_destination_values(review: dict[str, Any], primary_destination: str) -> list[str]:
    values = canonical_knowledge_bases([str(value) for value in review.get("approved_destinations") or [] if str(value)])
    if str(review.get("status") or "pending") == "pending":
        return values or [primary_destination]
    return values or [primary_destination]


def _destination_label(cfg: IntakeConfig, key: str) -> str:
    canonical = canonical_knowledge_base(key)
    return KNOWLEDGE_BASE_LABELS.get(canonical, canonical)


def _understanding(frontmatter: dict[str, Any], classified: ClassifiedItem) -> dict[str, Any]:
    values = dict(frontmatter.get("understanding") or {})
    if not values.get("material_type"):
        values["material_type"] = infer_material_type(classified.record.item, classified.classification)
    if not values.get("processing_plan"):
        values["processing_plan"] = infer_processing_plan(classified.record.item, classified.classification)
    if not values.get("why_saved"):
        values["why_saved"] = classified.classification.rationale
    values["why_saved"] = _knowledge_base_language(str(values.get("why_saved") or ""))
    return values


def _knowledge_base_language(value: str) -> str:
    return value.replace("destination signal", "knowledge base signal").replace("routing signal", "knowledge base signal")


def _destination_checkboxes(cfg: IntakeConfig, selected: list[str], recommended: str) -> str:
    selected_set = set(selected)
    rows = []
    for key in KNOWLEDGE_BASE_KEYS:
        if key not in cfg.destinations:
            continue
        badge = '<span class="recommended-badge">Recommended</span>' if key == recommended else ""
        rows.append(
            f"""<label class="kb-toggle"><input type="checkbox" name="approved_destinations" value="{escape(key)}" {_checked(key in selected_set)}> <span>{escape(KNOWLEDGE_BASE_LABELS[key])}</span>{badge}</label>"""
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
h1 a { color:var(--ink); text-decoration:none; }
h1 a:hover { text-decoration:underline; }
header p { margin:3px 0 0; color:var(--muted); font-size:13px; }
button { border:1px solid #0b5f59; background:var(--accent); color:#fff; height:34px; padding:0 12px; border-radius:6px; font-weight:650; cursor:pointer; }
button.secondary { background:#fff; color:var(--ink); border-color:var(--line); }
main { display:grid; grid-template-columns:minmax(280px, 360px) 1fr; min-height:calc(100vh - 64px); }
nav { border-right:1px solid var(--line); background:var(--panel); overflow:auto; max-height:calc(100vh - 64px); }
.item { display:block; padding:12px 14px; border-bottom:1px solid var(--line); color:var(--ink); text-decoration:none; }
.item strong { display:block; font-size:14px; line-height:1.25; font-weight:650; }
.item span { display:block; margin-top:5px; color:var(--muted); font-size:12px; }
.item.active { background:#fff; box-shadow:inset 3px 0 0 var(--accent); }
main > section { overflow:auto; max-height:calc(100vh - 64px); }
article { padding:22px; }
.review-top { margin-bottom:18px; }
.review-layout { display:grid; grid-template-columns:minmax(0, 1.35fr) minmax(320px, .65fr); gap:22px; align-items:start; }
.reader, .decision { min-width:0; }
.decision { border:1px solid var(--line); border-radius:8px; padding:16px; background:#fff; position:sticky; top:18px; }
.title-row { display:flex; align-items:flex-start; justify-content:space-between; gap:18px; padding-bottom:16px; border-bottom:1px solid var(--line); }
h2 { font-size:22px; line-height:1.2; margin:0 0 6px; letter-spacing:0; }
a { color:#175cd3; }
.source-separator { color:var(--muted); margin:0 7px; }
.source-details { margin-top:8px; max-width:920px; }
.source-details summary, .system-details summary { color:var(--muted); cursor:pointer; font-size:12px; font-weight:750; }
.source-details dl { margin-top:8px; }
.meta { color:var(--muted); white-space:nowrap; font-size:13px; }
form { margin:18px 0; }
form.decision-form { margin:0; }
.controls { display:grid; grid-template-columns: repeat(3, minmax(160px, 1fr)); gap:12px; align-items:end; }
label { display:block; font-size:12px; color:var(--muted); font-weight:650; }
select, textarea { width:100%; margin-top:5px; border:1px solid var(--line); border-radius:6px; padding:8px; font:inherit; color:var(--ink); background:#fff; }
textarea { resize:vertical; }
.check { display:flex; align-items:center; gap:8px; color:var(--ink); height:34px; }
.check input { width:16px; height:16px; }
.thinking { margin:12px 0; color:var(--ink); }
.thinking textarea { min-height:78px; background:#fbfcfd; }
.understanding { display:grid; gap:10px; margin:14px 0; padding:12px; border:1px solid var(--line); border-radius:6px; background:#fff; }
.understanding h4 { margin:0; color:var(--ink); font-size:13px; letter-spacing:0; }
.understanding textarea { background:#fbfcfd; }
.kb-decision { margin-top:14px; padding:14px; border:1px solid var(--line); border-radius:8px; background:#fbfcfd; }
.kb-decision h3 { margin-bottom:6px; }
.kb-decision .decision-question { margin:0 0 10px; }
.kb-decision .recommendation { margin-bottom:10px; }
.destination-picker { display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:8px; margin:10px 0 0; padding:0; border:0; background:transparent; }
.destination-picker legend { color:var(--muted); font-size:12px; font-weight:700; padding:0 4px; }
.kb-toggle { position:relative; display:flex; min-height:42px; align-items:center; gap:8px; color:var(--ink); font-size:13px; font-weight:650; border:1px solid var(--line); border-radius:8px; padding:9px 10px; background:#fff; cursor:pointer; }
.kb-toggle:hover { border-color:#7fbbb5; background:#f4fbfa; }
.kb-toggle:has(input:checked) { border-color:var(--accent); background:#ecfdf9; box-shadow:inset 0 0 0 1px var(--accent); }
.kb-toggle input { position:absolute; inset:0; opacity:0; cursor:pointer; }
.kb-toggle span { position:relative; }
.recommended-badge { position:relative; margin-left:auto; color:var(--accent); font-size:10px; font-weight:750; text-transform:uppercase; letter-spacing:0; }
.buttons { display:flex; gap:10px; margin-top:14px; }
.decision-buttons { align-items:center; flex-wrap:wrap; }
.suggested-actions { margin-bottom:14px; }
.suggested-actions ul { margin:0 0 12px; padding-left:18px; }
.suggested-actions li { margin:5px 0; font-size:13px; line-height:1.35; }
.system-details { margin:12px 0; padding-top:10px; border-top:1px solid var(--line); }
.system-details label { margin-top:10px; }
.source-lower { margin-top:22px; }
.source-lower:empty { display:none; }
.source-preview { border-top:1px solid var(--line); padding-top:18px; }
.source-preview iframe { width:100%; height:520px; border:1px solid var(--line); border-radius:8px; background:#fff; }
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
  main > section { max-height:none; }
  .review-layout, .controls, .split, .destination-picker { grid-template-columns:1fr; }
  .decision { position:static; }
  .title-row { display:block; }
  .meta { margin-top:8px; }
}
"""
