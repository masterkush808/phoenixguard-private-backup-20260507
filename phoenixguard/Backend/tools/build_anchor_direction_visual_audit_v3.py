from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote


REJECTION_REASON = "PG_ANCHOR_MASKED_PREFIX_MISSING_FINAL_VISIBLE_CANDLE"


def _load_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object: {path}")
    return value


def _href(path: Path, output_dir: Path) -> str:
    relative = os.path.relpath(path.resolve(), output_dir.resolve()).replace("\\", "/")
    return quote(relative, safe="/:@-._~")


def _case_artifact(case_dir: Path, name: str, fallback: Path | None = None) -> Path:
    candidate = case_dir / name
    if candidate.is_file():
        return candidate
    if fallback is not None and fallback.is_file():
        return fallback
    return candidate


def _accepted_record(
    score_path: Path,
    manifest_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    score = _load_json(score_path)
    manifest = _load_json(manifest_path)
    case_dir = manifest_path.parent
    proof = manifest["mask_proof"]
    rectangle = proof["rectangle"]
    source_path = Path(score["source_path"])
    mask_path = _case_artifact(case_dir, "masked_prefix.png", Path(score["mask_path"]))
    reveal_path = _case_artifact(case_dir, "revealed_actual.png", source_path)

    horizons: list[dict[str, Any]] = []
    for horizon_text, outcome in sorted(
        score["horizons"].items(), key=lambda item: int(item[0])
    ):
        horizons.append(
            {
                "h": int(horizon_text),
                "p": outcome["predicted_side"],
                "a": outcome["actual_side"],
                "ok": bool(outcome["correct"]),
                "p_up": float(outcome["probability_up"]),
                "support": int(outcome["supporting_families"]),
            }
        )

    hidden_count = int(score["hidden_candle_count"])
    if len(horizons) != hidden_count:
        raise ValueError(
            f"{score['case_id']}: {len(horizons)} outcomes for {hidden_count} hidden candles"
        )

    return {
        "status": "ACCEPTED",
        "case_id": score["case_id"],
        "image_id": score["image_id"],
        "family_id": score["family_id"],
        "fold": int(score["fold"]),
        "symbol": score.get("symbol") or "UNKNOWN",
        "timeframe": score.get("timeframe") or "UNKNOWN",
        "source_name": source_path.name,
        "visible_count": int(manifest["visible_prefix_candle_count"]),
        "hidden_count": hidden_count,
        "accuracy": float(score["accuracy"]),
        "correct_count": int(score["correct_count"]),
        "prediction_preceded_reveal": bool(score["prediction_preceded_reveal"]),
        "prediction_frozen_epoch_ms": int(score["prediction_frozen_epoch_ms"]),
        "reveal_started_epoch_ms": int(score["reveal_started_epoch_ms"]),
        "width": int(proof["analysis_width"]),
        "height": int(proof["analysis_height"]),
        "cut_x": int(rectangle["x1"]),
        "anchor_x": float(score["fixed_anchor"]["center_x_px"]),
        "anchor_y": float(score["fixed_anchor"]["close_y_px"]),
        "anchor_basis": score["fixed_anchor"]["basis"],
        "masked_href": _href(mask_path, output_dir),
        "revealed_href": _href(reveal_path, output_dir),
        "source_href": _href(source_path, output_dir),
        "scorecard_href": _href(score_path, output_dir),
        "prediction_href": _href(score_path.parent / "prediction_frozen.json", output_dir),
        "horizons": horizons,
    }


def _rejected_record(
    manifest_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    manifest = _load_json(manifest_path)
    case_dir = manifest_path.parent
    old_score_path = case_dir / "scorecard.json"
    old_score = _load_json(old_score_path) if old_score_path.is_file() else {}
    proof = manifest["mask_proof"]
    rectangle = proof["rectangle"]
    source_path = Path(old_score["source_path"]) if old_score.get("source_path") else Path()
    mask_path = _case_artifact(case_dir, "masked_prefix.png")
    reveal_path = _case_artifact(case_dir, "revealed_actual.png", source_path)

    return {
        "status": "REJECTED",
        "case_id": manifest["case_id"],
        "image_id": manifest["image_id"],
        "family_id": manifest["family_id"],
        "fold": int(manifest["fold"]),
        "symbol": old_score.get("symbol") or "UNKNOWN",
        "timeframe": old_score.get("timeframe") or "UNKNOWN",
        "source_name": source_path.name if source_path.name else "UNKNOWN",
        "visible_count": int(manifest["visible_prefix_candle_count"]),
        "hidden_count": int(manifest["hidden_future_candle_count"]),
        "width": int(proof["analysis_width"]),
        "height": int(proof["analysis_height"]),
        "cut_x": int(rectangle["x1"]),
        "reason": REJECTION_REASON,
        "corrected_prediction": None,
        "masked_href": _href(mask_path, output_dir),
        "revealed_href": _href(reveal_path, output_dir),
        "source_href": _href(source_path, output_dir) if source_path.name else "",
        "manifest_href": _href(manifest_path, output_dir),
        "legacy_scorecard_present": old_score_path.is_file(),
    }


def build_payload(
    corrected_root: Path,
    pure_root: Path,
    output_dir: Path,
) -> dict[str, Any]:
    manifest_paths = sorted((pure_root / "cases").glob("*/cutoff-*/case_manifest.json"))
    if not manifest_paths:
        raise FileNotFoundError(f"No case manifests found under {pure_root / 'cases'}")

    manifests_by_case: dict[str, Path] = {}
    image_ids: set[str] = set()
    family_ids: set[str] = set()
    for manifest_path in manifest_paths:
        manifest = _load_json(manifest_path)
        case_id = str(manifest["case_id"])
        manifests_by_case[case_id] = manifest_path
        image_ids.add(str(manifest["image_id"]))
        family_ids.add(str(manifest["family_id"]))

    accepted: list[dict[str, Any]] = []
    accepted_ids: set[str] = set()
    score_paths = sorted((corrected_root / "cases").glob("*/cutoff-*/scorecard.json"))
    if not score_paths:
        raise FileNotFoundError(f"No corrected scorecards found under {corrected_root / 'cases'}")

    for score_path in score_paths:
        score = _load_json(score_path)
        case_id = str(score["case_id"])
        manifest_path = manifests_by_case.get(case_id)
        if manifest_path is None:
            raise ValueError(f"Corrected scorecard has no matching chart-specific mask: {case_id}")
        accepted.append(_accepted_record(score_path, manifest_path, output_dir))
        accepted_ids.add(case_id)

    rejected = [
        _rejected_record(manifest_path, output_dir)
        for case_id, manifest_path in sorted(manifests_by_case.items())
        if case_id not in accepted_ids
    ]

    prediction_count = sum(len(case["horizons"]) for case in accepted)
    correct_count = sum(case["correct_count"] for case in accepted)
    exact_accuracy = correct_count / prediction_count if prediction_count else 0.0

    return {
        "schema_version": "PG_ANCHOR_DIRECTION_VISUAL_AUDIT_V3",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "contract": {
            "prediction_source": "EXISTING_FROZEN_CORRECTED_SCORECARDS_ONLY",
            "question": "IS_EACH_FUTURE_CLOSE_ABOVE_OR_BELOW_THE_SAME_FIXED_ANCHOR",
            "recomputed_predictions": False,
            "legacy_prediction_overlays_used": False,
            "rejection_reason": REJECTION_REASON,
        },
        "stats": {
            "source_screenshots": len(image_ids),
            "families": len(family_ids),
            "mask_cases": len(manifest_paths),
            "accepted_cases": len(accepted),
            "rejected_cases": len(rejected),
            "hidden_candle_predictions": prediction_count,
            "correct_predictions": correct_count,
            "exact_accuracy": round(exact_accuracy, 6),
        },
        "accepted": accepted,
        "rejected": rejected,
    }


HTML_TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PhoenixGuard Fixed-Anchor Evidence Room</title>
  <style>
    :root {
      --ink: #f1f3e8;
      --muted: #98a097;
      --panel: #101615;
      --panel-2: #151d1b;
      --line: #2b3733;
      --acid: #d7ff4f;
      --cyan: #55d6ff;
      --down: #ff665c;
      --up: #5ce6ae;
      --tie: #a7aaa5;
      --warn: #ffb84d;
      --bg: #070a09;
    }

    * { box-sizing: border-box; }
    html { background: var(--bg); color: var(--ink); }
    body {
      margin: 0;
      min-height: 100vh;
      font-family: Bahnschrift, "Aptos Narrow", "Arial Narrow", sans-serif;
      background:
        linear-gradient(rgba(85, 214, 255, .045) 1px, transparent 1px),
        linear-gradient(90deg, rgba(85, 214, 255, .045) 1px, transparent 1px),
        radial-gradient(circle at 76% 0%, rgba(215, 255, 79, .09), transparent 32rem),
        var(--bg);
      background-size: 32px 32px, 32px 32px, auto, auto;
    }

    button, input, select { font: inherit; }
    a { color: var(--cyan); }
    .shell { width: min(1760px, 100%); margin: 0 auto; padding: 28px; }
    .masthead { border-top: 4px solid var(--acid); padding: 24px 0 18px; }
    .kicker {
      color: var(--acid); text-transform: uppercase; letter-spacing: .18em;
      font-family: Consolas, monospace; font-size: 12px; font-weight: 700;
    }
    h1 { font-size: clamp(40px, 6vw, 88px); line-height: .88; margin: 14px 0 18px; max-width: 1050px; letter-spacing: -.045em; }
    .lead { max-width: 920px; color: #c6ccc4; font-size: 18px; line-height: 1.55; }

    .stats {
      display: grid; grid-template-columns: repeat(6, minmax(130px, 1fr));
      border: 1px solid var(--line); border-left: 0; margin: 26px 0;
    }
    .stat { padding: 17px 18px; border-left: 1px solid var(--line); background: rgba(16, 22, 21, .86); }
    .stat strong { display: block; font: 700 25px/1 Consolas, monospace; }
    .stat span { display: block; color: var(--muted); margin-top: 8px; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; }

    .contract {
      display: grid; grid-template-columns: 1.1fr 1fr; gap: 1px; background: var(--line);
      border: 1px solid var(--line); margin-bottom: 24px;
    }
    .contract > div { background: rgba(16, 22, 21, .96); padding: 20px; }
    .contract h2 { margin: 0 0 10px; font-size: 19px; }
    .contract p { color: #bbc1b9; margin: 0; line-height: 1.5; }
    .contract code { color: var(--warn); font-family: Consolas, monospace; }

    .toolbar {
      position: sticky; top: 0; z-index: 20; padding: 12px;
      display: grid; grid-template-columns: auto minmax(240px, 1fr) repeat(3, minmax(130px, auto)); gap: 10px;
      background: rgba(7, 10, 9, .94); border: 1px solid var(--line); backdrop-filter: blur(12px);
    }
    .tabs { display: flex; gap: 4px; }
    button, input, select {
      color: var(--ink); background: #111816; border: 1px solid #34413d; padding: 10px 12px;
    }
    button { cursor: pointer; }
    button:hover, button.active { border-color: var(--acid); color: var(--acid); }
    input { width: 100%; }

    .result-line { display: flex; justify-content: space-between; gap: 20px; margin: 22px 0 12px; color: var(--muted); }
    .result-line strong { color: var(--ink); }
    .case-list { display: grid; gap: 28px; }
    .case {
      background: rgba(12, 17, 16, .96); border: 1px solid var(--line); border-top: 3px solid var(--cyan);
      animation: reveal .25s ease both;
    }
    .case.rejected { border-top-color: var(--down); }
    @keyframes reveal { from { opacity: 0; transform: translateY(8px); } }
    .case-head {
      display: grid; grid-template-columns: 1fr auto; gap: 20px; padding: 18px 20px;
      border-bottom: 1px solid var(--line);
    }
    .case-id { margin: 0; font: 700 17px/1.25 Consolas, monospace; word-break: break-all; }
    .meta { color: var(--muted); margin-top: 8px; font-size: 13px; }
    .score { text-align: right; }
    .score strong { font: 700 29px/1 Consolas, monospace; }
    .score span { display: block; color: var(--muted); font-size: 11px; margin-top: 6px; text-transform: uppercase; }

    .visuals { display: grid; grid-template-columns: 1fr 1fr; gap: 1px; background: var(--line); }
    .visual { min-width: 0; background: #050706; padding: 14px; }
    .visual h3 { font: 700 12px/1 Consolas, monospace; letter-spacing: .08em; color: var(--muted); text-transform: uppercase; margin: 0 0 10px; }
    .image-stage { position: relative; width: 100%; overflow: hidden; background: #020303; }
    .image-stage img { width: 100%; height: auto; display: block; }
    .image-stage svg { position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }
    .image-stage:hover { outline: 1px solid var(--acid); }

    .evidence { padding: 18px 20px 21px; }
    .anchor-note { display: flex; flex-wrap: wrap; gap: 8px 18px; color: #bdc5bc; font: 12px/1.5 Consolas, monospace; }
    .anchor-note b { color: var(--acid); }
    .rejection {
      border-left: 4px solid var(--down); background: rgba(255, 102, 92, .08);
      padding: 15px 16px; line-height: 1.45;
    }
    .rejection strong { color: var(--down); font-family: Consolas, monospace; }

    .sequence { margin-top: 18px; overflow-x: auto; padding-bottom: 7px; }
    .cells { display: flex; gap: 3px; width: max-content; }
    .cell {
      width: 31px; height: 57px; border: 1px solid #46504d; display: grid;
      grid-template-rows: 15px 1fr 1fr; background: #090c0b; position: relative;
    }
    .cell.correct { border-color: rgba(92, 230, 174, .7); }
    .cell.wrong { border-color: rgba(255, 102, 92, .75); }
    .hz { color: #aeb5ae; font: 9px/15px Consolas, monospace; text-align: center; }
    .bar { font: 700 9px/19px Consolas, monospace; text-align: center; color: #06100c; }
    .bar.up { background: var(--up); }
    .bar.down { background: var(--down); }
    .bar.tie { background: var(--tie); }
    .legend { display: flex; flex-wrap: wrap; gap: 14px; margin-top: 9px; color: var(--muted); font: 11px Consolas, monospace; }
    .swatch { display: inline-block; width: 9px; height: 9px; margin-right: 5px; }

    .links { display: flex; flex-wrap: wrap; gap: 10px 18px; margin-top: 16px; font: 12px Consolas, monospace; }
    .pager { display: flex; justify-content: center; align-items: center; gap: 8px; margin: 28px 0 60px; }
    .pager span { color: var(--muted); padding: 0 8px; }
    .empty { border: 1px dashed var(--line); color: var(--muted); padding: 50px; text-align: center; }

    @media (max-width: 1050px) {
      .stats { grid-template-columns: repeat(3, 1fr); }
      .toolbar { grid-template-columns: 1fr 1fr; }
      .tabs { grid-column: 1 / -1; }
      .contract, .visuals { grid-template-columns: 1fr; }
    }
    @media (max-width: 640px) {
      .shell { padding: 14px; }
      .stats { grid-template-columns: repeat(2, 1fr); }
      .toolbar { position: static; grid-template-columns: 1fr; }
      .case-head { grid-template-columns: 1fr; }
      .score { text-align: left; }
    }
  </style>
</head>
<body>
  <main class="shell">
    <header class="masthead">
      <div class="kicker">PhoenixGuard / causal replay evidence</div>
      <h1>Fixed Anchor<br>Evidence Room</h1>
      <p class="lead">What the model saw while the future was blacked out, what it froze before reveal, and what every hidden candle actually did relative to one unchanged anchor.</p>
    </header>

    <section class="stats" id="stats"></section>

    <section class="contract">
      <div>
        <h2>Accepted evidence</h2>
        <p>The cyan line is the close geometry of the final visible candle. Every <b>P</b> prediction was frozen before reveal. Every <b>A</b> outcome asks only whether that hidden close finished above or below that exact line.</p>
      </div>
      <div>
        <h2>Why a mask was rejected</h2>
        <p>The corrected extractor could not reacquire the final visible anchor candle from the masked pixels. Using coordinates recovered from the unmasked image would leak future-side analysis. Those cases therefore have <code>NO CORRECTED PREDICTION</code>, and the reveal is shown only for inspection.</p>
      </div>
    </section>

    <section class="toolbar">
      <div class="tabs">
        <button id="accepted-tab" type="button">Accepted predictions</button>
        <button id="rejected-tab" type="button">Rejected masks</button>
      </div>
      <input id="search" type="search" placeholder="Search case, pair, timeframe, image...">
      <select id="symbol"><option value="">All pairs</option></select>
      <select id="timeframe"><option value="">All timeframes</option></select>
      <select id="sort">
        <option value="case">Case order</option>
        <option value="accuracy-asc">Lowest accuracy first</option>
        <option value="accuracy-desc">Highest accuracy first</option>
        <option value="hidden-desc">Longest hidden horizon</option>
      </select>
    </section>

    <div class="result-line"><span id="result-count"></span><strong id="page-title"></strong></div>
    <section class="case-list" id="case-list"></section>
    <nav class="pager" id="pager"></nav>
  </main>

  <script>
    const DATA = __DATA__;
    const PAGE_SIZE = 8;
    let mode = location.hash === '#accepted' ? 'accepted' : 'rejected';
    let page = 1;

    const $ = (selector) => document.querySelector(selector);
    const escapeHtml = (value) => String(value ?? '')
      .replaceAll('&', '&amp;').replaceAll('<', '&lt;').replaceAll('>', '&gt;')
      .replaceAll('"', '&quot;').replaceAll("'", '&#039;');
    const percent = (value) => `${(Number(value) * 100).toFixed(2)}%`;
    const sideClass = (side) => String(side).toLowerCase();

    function populateFilters() {
      const all = [...DATA.accepted, ...DATA.rejected];
      const symbols = [...new Set(all.map(c => c.symbol))].sort();
      const timeframes = [...new Set(all.map(c => c.timeframe))].sort();
      $('#symbol').insertAdjacentHTML('beforeend', symbols.map(v => `<option>${escapeHtml(v)}</option>`).join(''));
      $('#timeframe').insertAdjacentHTML('beforeend', timeframes.map(v => `<option>${escapeHtml(v)}</option>`).join(''));
    }

    function renderStats() {
      const s = DATA.stats;
      const items = [
        [s.source_screenshots, 'source screenshots'], [s.mask_cases, 'chart-specific masks'],
        [s.accepted_cases, 'accepted masks'], [s.rejected_cases, 'rejected masks'],
        [s.hidden_candle_predictions.toLocaleString(), 'frozen predictions'], [percent(s.exact_accuracy), 'exact accuracy']
      ];
      $('#stats').innerHTML = items.map(([value, label]) => `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`).join('');
    }

    function overlay(caseData, rejected) {
      const cut = caseData.cut_x;
      const width = caseData.width;
      const height = caseData.height;
      const cutLabelX = Math.max(12, Math.min(width - 260, cut + 10));
      let marks = `<line x1="${cut}" y1="0" x2="${cut}" y2="${height}" stroke="#ffb84d" stroke-width="3" stroke-dasharray="10 7"/>`;
      marks += `<rect x="${cutLabelX}" y="12" width="245" height="28" fill="rgba(7,10,9,.9)" stroke="#ffb84d"/>`;
      marks += `<text x="${cutLabelX + 9}" y="31" fill="#ffb84d" font-family="Consolas" font-size="14">FUTURE STARTS HERE</text>`;
      if (rejected) {
        const x = Math.max(12, Math.min(width - 330, cut - 330));
        marks += `<rect x="${x}" y="50" width="320" height="34" fill="rgba(7,10,9,.94)" stroke="#ff665c" stroke-width="2"/>`;
        marks += `<text x="${x + 10}" y="72" fill="#ff665c" font-family="Consolas" font-size="14">ANCHOR NOT RECOVERED FROM MASK</text>`;
      } else {
        const y = caseData.anchor_y;
        marks += `<line x1="0" y1="${y}" x2="${width}" y2="${y}" stroke="#55d6ff" stroke-width="3"/>`;
        marks += `<circle cx="${caseData.anchor_x}" cy="${y}" r="7" fill="#d7ff4f" stroke="#07100d" stroke-width="2"/>`;
        const yText = Math.max(18, y - 9);
        marks += `<text x="12" y="${yText}" fill="#55d6ff" font-family="Consolas" font-size="14">FIXED ANCHOR CLOSE y=${caseData.anchor_y.toFixed(1)}</text>`;
      }
      return `<svg viewBox="0 0 ${width} ${height}" preserveAspectRatio="none">${marks}</svg>`;
    }

    function imagePanel(title, href, caseData, rejected) {
      return `<div class="visual"><h3>${title}</h3><a href="${href}" target="_blank" title="Open full-size image"><div class="image-stage"><img loading="lazy" src="${href}" alt="${escapeHtml(title)} for ${escapeHtml(caseData.case_id)}">${overlay(caseData, rejected)}</div></a></div>`;
    }

    function sequence(caseData) {
      const cells = caseData.horizons.map(h => {
        const title = `H${h.h} | predicted ${h.p} (${percent(h.p_up)} UP) | actual ${h.a} | ${h.ok ? 'CORRECT' : 'WRONG'} | ${h.support} families`;
        return `<div class="cell ${h.ok ? 'correct' : 'wrong'}" title="${escapeHtml(title)}"><span class="hz">H${h.h}</span><span class="bar ${sideClass(h.p)}">P ${h.p[0]}</span><span class="bar ${sideClass(h.a)}">A ${h.a[0]}</span></div>`;
      }).join('');
      return `<div class="sequence"><div class="cells">${cells}</div></div>
        <div class="legend"><span><i class="swatch" style="background:var(--up)"></i>UP</span><span><i class="swatch" style="background:var(--down)"></i>DOWN</span><span><i class="swatch" style="background:var(--tie)"></i>TIE</span><span>P = frozen prediction</span><span>A = revealed actual</span><span>green border = correct</span><span>red border = wrong</span><span>hover any horizon for exact probability</span></div>`;
    }

    function acceptedCard(c) {
      const frozenGap = c.reveal_started_epoch_ms - c.prediction_frozen_epoch_ms;
      return `<article class="case">
        <header class="case-head"><div><h2 class="case-id">${escapeHtml(c.case_id)}</h2><div class="meta">${escapeHtml(c.symbol)} / ${escapeHtml(c.timeframe)} | ${escapeHtml(c.source_name)} | fold ${c.fold} | ${c.visible_count} visible + ${c.hidden_count} hidden</div></div><div class="score"><strong>${percent(c.accuracy)}</strong><span>${c.correct_count} / ${c.hidden_count} correct</span></div></header>
        <div class="visuals">${imagePanel('1 / Masked input available to predictor', c.masked_href, c, false)}${imagePanel('2 / Future revealed after prediction freeze', c.revealed_href, c, false)}</div>
        <div class="evidence"><div class="anchor-note"><span><b>ANCHOR</b> (${c.anchor_x.toFixed(1)}, ${c.anchor_y.toFixed(1)})</span><span><b>BASIS</b> ${escapeHtml(c.anchor_basis)}</span><span><b>FREEZE LEAD</b> ${frozenGap} ms before reveal</span><span><b>CROSS-CHART MASKING</b> none</span></div>${sequence(c)}
        <div class="links"><a href="${c.masked_href}" target="_blank">masked PNG</a><a href="${c.revealed_href}" target="_blank">revealed PNG</a><a href="${c.prediction_href}" target="_blank">frozen prediction JSON</a><a href="${c.scorecard_href}" target="_blank">outcome scorecard JSON</a><a href="${c.source_href}" target="_blank">source screenshot</a></div></div>
      </article>`;
    }

    function rejectedCard(c) {
      return `<article class="case rejected">
        <header class="case-head"><div><h2 class="case-id">${escapeHtml(c.case_id)}</h2><div class="meta">${escapeHtml(c.symbol)} / ${escapeHtml(c.timeframe)} | ${escapeHtml(c.source_name)} | fold ${c.fold} | ${c.visible_count} visible + ${c.hidden_count} hidden</div></div><div class="score"><strong>NO CALL</strong><span>corrected run</span></div></header>
        <div class="visuals">${imagePanel('1 / Masked input that failed anchor recovery', c.masked_href, c, true)}${imagePanel('2 / Post-test reveal for human inspection only', c.revealed_href, c, true)}</div>
        <div class="evidence"><div class="rejection"><strong>${escapeHtml(c.reason)}</strong><br>The corrected run emitted no UP/DOWN prediction and no accuracy score for this mask. The folder may contain files from the superseded replay; those legacy predictions are deliberately excluded from this viewer and from the 51.93% result.</div>
        <div class="links"><a href="${c.masked_href}" target="_blank">masked PNG</a><a href="${c.revealed_href}" target="_blank">revealed PNG</a><a href="${c.manifest_href}" target="_blank">mask proof JSON</a>${c.source_href ? `<a href="${c.source_href}" target="_blank">source screenshot</a>` : ''}</div></div>
      </article>`;
    }

    function filteredCases() {
      const search = $('#search').value.trim().toLowerCase();
      const symbol = $('#symbol').value;
      const timeframe = $('#timeframe').value;
      const sort = $('#sort').value;
      let cases = DATA[mode].filter(c => {
        const text = `${c.case_id} ${c.image_id} ${c.symbol} ${c.timeframe} ${c.source_name}`.toLowerCase();
        return (!search || text.includes(search)) && (!symbol || c.symbol === symbol) && (!timeframe || c.timeframe === timeframe);
      });
      cases = [...cases].sort((a, b) => {
        if (sort === 'accuracy-asc') return (a.accuracy ?? 2) - (b.accuracy ?? 2);
        if (sort === 'accuracy-desc') return (b.accuracy ?? -1) - (a.accuracy ?? -1);
        if (sort === 'hidden-desc') return b.hidden_count - a.hidden_count;
        return a.case_id.localeCompare(b.case_id);
      });
      return cases;
    }

    function renderPager(total) {
      const pages = Math.max(1, Math.ceil(total / PAGE_SIZE));
      page = Math.min(page, pages);
      $('#pager').innerHTML = `<button type="button" data-page="1">First</button><button type="button" data-page="${Math.max(1, page - 1)}">Previous</button><span>Page ${page} / ${pages}</span><button type="button" data-page="${Math.min(pages, page + 1)}">Next</button><button type="button" data-page="${pages}">Last</button>`;
      $('#pager').querySelectorAll('button').forEach(button => button.addEventListener('click', () => { page = Number(button.dataset.page); render(); scrollTo({ top: $('.toolbar').offsetTop, behavior: 'smooth' }); }));
    }

    function render() {
      const cases = filteredCases();
      const start = (page - 1) * PAGE_SIZE;
      const visible = cases.slice(start, start + PAGE_SIZE);
      $('#accepted-tab').classList.toggle('active', mode === 'accepted');
      $('#rejected-tab').classList.toggle('active', mode === 'rejected');
      $('#page-title').textContent = mode === 'accepted' ? 'Frozen predictions versus outcomes' : 'Masks rejected before prediction';
      $('#result-count').innerHTML = `<strong>${cases.length}</strong> matching cases`;
      $('#case-list').innerHTML = visible.length ? visible.map(c => mode === 'accepted' ? acceptedCard(c) : rejectedCard(c)).join('') : '<div class="empty">No cases match these filters.</div>';
      renderPager(cases.length);
    }

    function setMode(next) {
      mode = next; page = 1; location.hash = next; render();
    }

    $('#accepted-tab').addEventListener('click', () => setMode('accepted'));
    $('#rejected-tab').addEventListener('click', () => setMode('rejected'));
    ['search', 'symbol', 'timeframe', 'sort'].forEach(id => $(`#${id}`).addEventListener(id === 'search' ? 'input' : 'change', () => { page = 1; render(); }));
    populateFilters(); renderStats(); render();
  </script>
</body>
</html>
'''


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a visual audit for the corrected fixed-anchor replay."
    )
    repo_root = Path(__file__).resolve().parents[2]
    parser.add_argument(
        "--corrected-root",
        type=Path,
        default=repo_root / ".codex_runtime" / "anchor_direction_only_v3",
    )
    parser.add_argument(
        "--pure-root",
        type=Path,
        default=repo_root / ".codex_runtime" / "pure_masked_future",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=repo_root
        / ".codex_runtime"
        / "anchor_direction_visual_audit_v3"
        / "index.html",
    )
    args = parser.parse_args()

    output_path = args.output.resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload(
        args.corrected_root.resolve(),
        args.pure_root.resolve(),
        output_path.parent,
    )
    compact_payload = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).replace(
        "</", "<\\/"
    )
    output_path.write_text(
        HTML_TEMPLATE.replace("__DATA__", compact_payload),
        encoding="utf-8",
        newline="\n",
    )

    print(
        json.dumps(
            {
                "status": "COMPLETE",
                "output": str(output_path),
                **payload["stats"],
                "recomputed_predictions": False,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
