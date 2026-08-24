"""Replay recorded window-tracker sessions through the V3 book strategist.

Rebuilds the book strategy forecast control from the evidence persisted in each
decision artifact (tracked candles, trendlines, zones, HTF context, pair DNA)
and runs the published verdict path end-to-end. The report attributes every
non-actionable frame to the gate that starved it so resolution regressions are
diagnosable from real sessions instead of synthetic fixtures.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys
from typing import Any, cast

_TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_TOOLS_DIR))

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

from phoenixguard.decision.book_rule_action_signal_v3 import (  # noqa: E402
    build_book_rule_action_signal_v3,
)
from phoenixguard.decision.book_strategy_forecast_v3 import (  # noqa: E402
    build_book_strategy_forecast_control_v3,
)


def _rows(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [
        cast("dict[str, Any]", row)
        for row in cast("list[object]", value)
        if isinstance(row, dict)
    ]


def _artifact_frame(path: Path) -> int:
    stem = path.name.split("_", 1)[0]
    try:
        return int(stem)
    except ValueError:
        return -1


def load_artifacts(session_dir: Path) -> list[Path]:
    artifacts = sorted(
        session_dir.glob("artifacts/*_decision.json"),
        key=_artifact_frame,
    )
    return [path for path in artifacts if _artifact_frame(path) >= 0]


def build_replay_context(path: Path) -> dict[str, Any] | None:
    """Rebuild the forecast control and verdict for one decision artifact."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    summary: dict[str, Any] = payload.get("tracking_summary") or {}
    latest_signal: dict[str, Any] = payload.get("latest_signal") or {}
    book_signal: dict[str, Any] = summary.get("book_rule_action_signal_v3") or latest_signal.get(
        "book_rule_action_signal_v3"
    ) or {}
    candles = _rows(summary.get("tracked_candles"))
    trendlines = _rows(summary.get("trendlines_v3"))
    zones = _rows(summary.get("support_resistance_zones"))
    market_study: dict[str, Any] = summary.get("market_study_v3") or {}
    if not candles:
        return None
    pair = str(latest_signal.get("market") or payload.get("market") or "UNKNOWN")
    timeframe = str(
        latest_signal.get("focus_timeframe")
        or latest_signal.get("high_frequency_timeframe_source")
        or "M5"
    )
    frame_id = int(latest_signal.get("published_epoch") or _artifact_frame(path))
    chart_image: dict[str, Any] = summary.get("chart_region") or {}
    bounds = [
        float(chart_image.get("x") or 0.0),
        float(chart_image.get("y") or 0.0),
        float(chart_image.get("x") or 0.0) + float(chart_image.get("width") or 1280.0),
        float(chart_image.get("y") or 0.0) + float(chart_image.get("height") or 720.0),
    ]
    control = build_book_strategy_forecast_control_v3(
        candles=candles,
        timeframe=timeframe,
        trendlines=trendlines,
        support_resistance_zones=zones,
        behavior_payload=market_study.get("behavior"),
        pair_dna_context=market_study.get("pair_dna"),
        session_context=None,
        news_context=None,
        higher_timeframe_context=latest_signal.get("major_trend_context"),
    )
    verdict = build_book_rule_action_signal_v3(
        control=control,
        candles=candles,
        trendlines=trendlines,
        support_resistance_zones=zones,
        pair=pair,
        timeframe=timeframe,
        frame_id=frame_id,
        closed_candle_key=str(market_study.get("closed_candle_key") or ""),
        closed_candle_sequence=int(market_study.get("closed_candle_sequence") or 0),
        market_selector_visual_fingerprint=str(
            latest_signal.get("market_selector_visual_fingerprint") or ""
        ),
        chart_bounds=bounds,
        identity_confirmed=True,
    )
    return {
        "frame": _artifact_frame(path),
        "pair": pair,
        "timeframe": timeframe,
        "candle_count": len(candles),
        "published": {
            key: book_signal.get(key)
            for key in (
                "status",
                "action",
                "watch_side",
                "actionable",
                "playbook",
                "confidence",
                "confluence_count",
            )
        },
        "control": control,
        "verdict": verdict,
    }


def replay_artifact(path: Path) -> dict[str, Any]:
    context = build_replay_context(path)
    if context is None:
        return {
            "frame": _artifact_frame(path),
            "artifact": path.name,
            "candle_count": 0,
            "published": {},
            "replayed": {},
            "starvation": {"reason": "MISSING_REPLAY_INPUTS", "detail": ""},
        }
    verdict = context["verdict"]
    starvation: dict[str, Any] = {
        "reason": "RESOLVED" if verdict.get("actionable") else str(verdict.get("status")),
        "detail": str(verdict.get("scenario")),
        "active_strategy_ids": [
            row.get("strategy_id")
            for row in _rows(verdict.get("strategy_report"))
            if row.get("status") == "ACTIVE"
        ],
        "confluence_count": verdict.get("confluence_count"),
    }
    return {
        "frame": context["frame"],
        "artifact": path.name,
        "pair": context["pair"],
        "timeframe": context["timeframe"],
        "candle_count": context["candle_count"],
        "published": context["published"],
        "replayed": {
            key: verdict.get(key)
            for key in (
                "status",
                "action",
                "watch_side",
                "actionable",
                "playbook",
                "confidence",
                "confluence_count",
                "scenario",
                "entry_profile",
            )
        },
        "starvation": starvation,
    }


def summarize(reports: list[dict[str, Any]]) -> dict[str, Any]:
    replayed_status = Counter(str(row["replayed"].get("status")) for row in reports)
    replayed_playbook = Counter(str(row["replayed"].get("playbook")) for row in reports)
    actionable_frames = sum(bool(row["replayed"].get("actionable")) for row in reports)
    published_actionable = sum(
        bool(cast("dict[str, Any]", row["published"] or {}).get("actionable"))
        for row in reports
    )
    return {
        "frames": len(reports),
        "published_actionable_frames": published_actionable,
        "replayed_actionable_frames": actionable_frames,
        "replayed_status_histogram": dict(replayed_status),
        "replayed_playbook_histogram": dict(replayed_playbook),
        "resolution_rate_percent": round(100.0 * actionable_frames / max(1, len(reports)), 1),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("session_dir", type=Path, help="Window tracker session directory.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON only.")
    args = parser.parse_args(argv)

    reports = [replay_artifact(path) for path in load_artifacts(args.session_dir)]
    summary = summarize(reports)
    if args.json:
        print(json.dumps({"summary": summary, "frames": reports}, indent=2))
        return 0
    print(json.dumps(summary, indent=2))
    for row in reports:
        replayed = row["replayed"]
        starve = row["starvation"]
        print(
            f"frame {row['frame']:>6} | {row['pair']:<14} {row['timeframe']:<4} | "
            f"candles={row['candle_count']:>3} | "
            f"published={str(cast('dict[str, Any]', row['published'] or {}).get('playbook')):<28} "
            f"{str(cast('dict[str, Any]', row['published'] or {}).get('actionable')):<5} | "
            f"replayed={str(replayed.get('playbook')):<28} "
            f"{str(replayed.get('status')):<34} conf={replayed.get('confidence')} | "
            f"{starve.get('reason')}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
