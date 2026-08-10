"""Static visual gallery for masked-before and revealed-after evidence."""

from __future__ import annotations

from html import escape
import json
import os
from pathlib import Path
from typing import Any, Mapping, cast


PURE_GALLERY_SCHEMA_VERSION = "PG_PURE_MASKED_FUTURE_GALLERY_V3"


def _mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return dict(cast(Mapping[str, Any], value))


def _number(value: object, default: float = 0.0) -> float:
    try:
        return float(cast(Any, value))
    except (TypeError, ValueError, OverflowError):
        return float(default)


def _relative(path: object, output: Path) -> str:
    candidate = Path(str(path)).resolve()
    return Path(os.path.relpath(candidate, output.parent.resolve())).as_posix()


def render_pure_masked_future_gallery_v3(
    run_dir: str | Path,
    output_path: str | Path,
) -> Path:
    root = Path(run_dir).resolve()
    output = Path(output_path).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    scorecards: list[dict[str, Any]] = []
    for path in sorted(root.glob("cases/*/*/scorecard.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, Mapping):
            row = dict(cast(Mapping[str, Any], payload))
            row["_scorecard_path"] = str(path)
            scorecards.append(row)
    cards: list[str] = []
    for score in scorecards:
        artifacts = _mapping(score.get("artifacts"))
        horizon_rows: list[str] = []
        for horizon, raw in sorted(
            _mapping(score.get("horizons")).items(),
            key=lambda item: int(item[0]),
        ):
            row = _mapping(raw)
            result = "correct" if row.get("majority_correct") else "wrong"
            horizon_rows.append(
                "<tr>"
                f"<td>{escape(horizon)}</td>"
                f"<td>{escape(str(row.get('predicted_side') or 'REST'))}</td>"
                f"<td>{escape(str(row.get('actual_majority_side') or 'REST'))}</td>"
                f"<td class='{result}'>{'YES' if result == 'correct' else 'NO'}</td>"
                f"<td>{100.0 * _number(row.get('candle_token_similarity')):.0f}%</td>"
                "</tr>"
            )
        card_class = "strong" if _number(score.get("overall_score")) >= 0.65 else "weak"
        cards.append(
            f"""
<article class="case {card_class}" data-pair="{escape(str(score.get('symbol') or 'UNKNOWN'))}" data-phase="{escape(str(score.get('market_phase') or 'UNKNOWN'))}">
  <header>
    <div><span class="eyebrow">{escape(str(score.get('symbol') or 'UNKNOWN'))} / {escape(str(score.get('timeframe') or 'UNKNOWN'))}</span>
    <h2>{escape(str(score.get('cutoff_id') or 'cutoff'))}</h2></div>
    <strong>{100.0 * _number(score.get('overall_score')):.1f}%</strong>
  </header>
  <div class="visuals">
    <figure><img loading="lazy" src="{escape(_relative(artifacts.get('masked_prefix'), output))}" alt="masked prefix"><figcaption>1. Masked prefix - future physically hidden</figcaption></figure>
    <figure><img loading="lazy" src="{escape(_relative(artifacts.get('prediction_before_reveal'), output))}" alt="frozen prediction"><figcaption>2. Prediction before reveal - frozen</figcaption></figure>
    <figure><img loading="lazy" src="{escape(_relative(artifacts.get('revealed_actual'), output))}" alt="revealed actual"><figcaption>3. Revealed actual</figcaption></figure>
    <figure><img loading="lazy" src="{escape(_relative(artifacts.get('prediction_vs_actual'), output))}" alt="prediction versus actual"><figcaption>4. Prediction vs actual</figcaption></figure>
  </div>
  <div class="details">
    <table><thead><tr><th>H</th><th>Predicted</th><th>Actual</th><th>Right</th><th>Token</th></tr></thead><tbody>{''.join(horizon_rows)}</tbody></table>
    <dl><dt>Phase</dt><dd>{escape(str(score.get('market_phase') or 'UNKNOWN'))}</dd><dt>Frozen</dt><dd>{escape(str(score.get('prediction_frozen_epoch_ms') or ''))}</dd><dt>Revealed</dt><dd>{escape(str(score.get('reveal_started_epoch_ms') or ''))}</dd></dl>
  </div>
</article>
"""
        )
    document = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>PhoenixGuard Pure Masked-Future Gallery</title>
<style>
:root{{--ink:#101418;--paper:#eef0e8;--line:#bdc3b5;--blue:#007fba;--orange:#d96c22;--green:#087b4f;--red:#b72c36}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font-family:"IBM Plex Mono","Courier New",monospace}}
.mast{{position:sticky;top:0;z-index:5;display:flex;justify-content:space-between;gap:24px;align-items:end;padding:22px 3vw;background:rgba(238,240,232,.96);border-bottom:2px solid var(--ink);backdrop-filter:blur(10px)}}
.mast h1{{margin:4px 0 0;font:800 clamp(24px,4vw,52px)/.92 Georgia,serif;letter-spacing:-.04em}} .mast p{{max-width:620px;margin:0;font-size:12px;line-height:1.55}}
.eyebrow{{font-size:10px;text-transform:uppercase;letter-spacing:.16em;color:#566052}}
main{{display:grid;gap:28px;padding:32px 3vw 80px}} .case{{border:1px solid var(--line);background:#f8f8f2;box-shadow:8px 8px 0 #d7dbcf}}
.case.strong{{border-top:5px solid var(--green)}} .case.weak{{border-top:5px solid var(--red)}}
.case>header{{display:flex;justify-content:space-between;align-items:center;padding:16px 18px;border-bottom:1px solid var(--line)}} .case h2{{margin:3px 0 0;font-size:15px}} .case>header strong{{font:700 28px Georgia,serif}}
.visuals{{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:1px;background:var(--line)}} figure{{margin:0;background:#0a0d0f}} figure img{{display:block;width:100%;aspect-ratio:16/8;object-fit:contain}} figcaption{{padding:8px 10px;background:#151a1d;color:#dfe7df;font-size:10px}}
.details{{display:grid;grid-template-columns:minmax(0,2fr) minmax(220px,1fr);gap:24px;padding:18px}} table{{width:100%;border-collapse:collapse;font-size:11px}} th,td{{padding:7px;border-bottom:1px solid var(--line);text-align:left}} .correct{{color:var(--green);font-weight:800}} .wrong{{color:var(--red);font-weight:800}} dl{{display:grid;grid-template-columns:auto 1fr;gap:8px 14px;margin:0;font-size:10px}} dt{{color:#687265}} dd{{margin:0;overflow-wrap:anywhere}}
@media(max-width:800px){{.mast{{position:static;display:block}}.mast p{{margin-top:12px}}.visuals{{grid-template-columns:1fr}}.details{{grid-template-columns:1fr}}}}
</style>
</head>
<body>
<header class="mast"><div><span class="eyebrow">PhoenixGuard V3 / Causal vision replay</span><h1>Masked before.<br>Scored after.</h1></div><p>{len(scorecards)} frozen predictions. Blue paths were drawn while the future pixels were hidden. Orange paths and horizon verdicts were added only after reveal.</p></header>
<main>{''.join(cards) if cards else '<p>No completed scorecards were found.</p>'}</main>
</body></html>"""
    output.write_text(document, encoding="utf-8")
    return output


__all__ = ["PURE_GALLERY_SCHEMA_VERSION", "render_pure_masked_future_gallery_v3"]
