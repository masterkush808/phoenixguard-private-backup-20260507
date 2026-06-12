"""Replay script: dry-run evaluation of signal payloads through parse_trade_signal().

Usage: python scripts/replay_signals.py <jsonl_path> [--limit N]

This imports parse_trade_signal from `shooter.py` safely (module import doesn't start execution).
Outputs a replay_trace.log and prints a short summary.
"""
import argparse
import json
import logging
from pathlib import Path
import os
import time
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

import importlib.util

from phoenixguard.execution.timing import TimingProfile, validate_timing_event

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("replay_harness")
file_handler = logging.FileHandler("replay_trace.log")
file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))
logger.addHandler(file_handler)


@dataclass(frozen=True)
class ReplayEvaluation:
    signal_id: str
    accepted: bool
    reason_codes: tuple[str, ...]
    side: str
    symbol: str
    timeframe: str
    setup_type: str
    recommended_expiry_seconds: int
    observed_expiry_seconds: int
    observed_entry_age_seconds: int


def _load_parse_trade_signal():
    shooter_path = Path(__file__).resolve().parents[1] / "shooter.py"
    spec = importlib.util.spec_from_file_location("shooter_module", str(shooter_path))
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import parse_trade_signal from {shooter_path}")
    shooter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(shooter)  # type: ignore[union-attr]
    return getattr(shooter, "parse_trade_signal")


def read_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def _normalize_label(value: object, default: str = "") -> str:
    label = str(value if value is not None else default).strip().lower()
    return label or default


def _upper_label(value: object, default: str = "") -> str:
    label = str(value if value is not None else default).strip().upper()
    return label or default


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def _is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y", "support", "resistance", "blocked"}


def normalize_replay_event(event: Mapping[str, Any]) -> dict[str, Any]:
    chart_state = event.get("chart_state")
    chart = chart_state if isinstance(chart_state, Mapping) else {}
    profile = event.get("profile")
    profile_map = profile if isinstance(profile, Mapping) else {}
    timing = event.get("execution_timing")
    timing_map = timing if isinstance(timing, Mapping) else {}

    setup_type = event.get(
        "setup_type",
        event.get(
            "structure_setup",
            chart.get("structure_setup", profile_map.get("structure_setup", chart.get("entry_type", "continuation"))),
        ),
    )
    return {
        **event,
        "signal_id": str(event.get("signal_id", event.get("id", "")) or ""),
        "side": _upper_label(event.get("side", event.get("execution_action", event.get("action", "")))),
        "symbol": str(event.get("symbol", event.get("pair", "*")) or "*"),
        "timeframe": str(event.get("timeframe", event.get("tf", "*")) or "*"),
        "setup_type": _normalize_label(setup_type, "continuation"),
        "expiry_seconds": event.get("expiry_seconds", timing_map.get("recommended_expiry_seconds", 0)),
        "entry_age_seconds": event.get(
            "entry_age_seconds",
            event.get("setup_age_seconds", timing_map.get("entry_age_seconds", timing_map.get("setup_age_seconds", 0))),
        ),
        "support_proximity": event.get("support_proximity", chart.get("support_proximity", 0.0)),
        "resistance_proximity": event.get("resistance_proximity", chart.get("resistance_proximity", 0.0)),
        "fakeout_probability": event.get(
            "fakeout_probability",
            chart.get("fakeout_probability", profile_map.get("fakeout_probability", 0.0)),
        ),
        "outcome": _normalize_label(event.get("outcome", event.get("result", ""))),
    }


def evaluate_replay_event(
    event: Mapping[str, Any],
    profiles: Mapping[str, TimingProfile] | None = None,
) -> ReplayEvaluation:
    normalized = normalize_replay_event(event)
    timing = validate_timing_event(normalized, profiles)
    reasons = list(timing.reason_codes)
    side = str(normalized["side"])

    if side == "SELL" and _as_float(normalized.get("support_proximity")) >= 0.75:
        reasons.append("sell_into_support")
    if side == "BUY" and _as_float(normalized.get("resistance_proximity")) >= 0.75:
        reasons.append("buy_into_resistance")
    if _as_float(normalized.get("fakeout_probability")) >= 0.70:
        reasons.append("fakeout_risk")
    if _is_truthy(normalized.get("known_loser")) or normalized.get("outcome") in {"loss", "losing"}:
        reasons.append("known_losing_replay")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return ReplayEvaluation(
        signal_id=str(normalized["signal_id"]),
        accepted=not unique_reasons,
        reason_codes=unique_reasons,
        side=side,
        symbol=str(normalized["symbol"]),
        timeframe=str(normalized["timeframe"]),
        setup_type=str(normalized["setup_type"]),
        recommended_expiry_seconds=timing.recommended_expiry_seconds,
        observed_expiry_seconds=timing.observed_expiry_seconds,
        observed_entry_age_seconds=timing.observed_entry_age_seconds,
    )


def evaluate_replay_events(
    events: Iterable[Mapping[str, Any]],
    profiles: Mapping[str, TimingProfile] | None = None,
) -> list[ReplayEvaluation]:
    return [evaluate_replay_event(event, profiles) for event in events]


def _acquire_lock(lock_path: Path) -> bool:
    if lock_path.exists():
        return False
    try:
        with lock_path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps({"pid": os.getpid(), "ts": time.time()}))
        return True
    except Exception:
        return False


def _release_lock(lock_path: Path) -> None:
    try:
        if lock_path.exists():
            lock_path.unlink()
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="path to jsonl file to replay")
    parser.add_argument("--generate-samples", action="store_true", help="Run a few synthetic sample payloads instead of reading a file")
    parser.add_argument("--limit", type=int, default=5000, help="max rows to process (-1 for no limit)")
    parser.add_argument("--collect-failures", type=str, default="data/failing_trades.jsonl", help="append accepted rows to this file for inspection")
    args = parser.parse_args()

    processed = 0
    accepted = 0
    lock_path = Path(__file__).with_name(".replay_signals.lock")
    have_lock = False
    if not args.generate_samples:
        have_lock = _acquire_lock(lock_path)
        if not have_lock:
            print("Another replay run appears active. Exiting to avoid concurrent runs.")
            return 3
    failures_out = None
    if args.collect_failures:
        try:
            failures_out = open(args.collect_failures, "a", encoding="utf-8")
        except Exception:
            failures_out = None
    parse_trade_signal = _load_parse_trade_signal()

    if args.generate_samples:
        samples = [
            {"signal_id": "s1", "actionable": True, "execution_action": "BUY", "expiry_seconds": 60},
            {"signal_id": "s2", "actionable": True, "execution_action": "SELL", "expiry_seconds": 30},
            {"signal_id": "s3", "actionable": False, "execution_action": "BUY", "expiry_seconds": 30},
        ]
        for row in samples:
            try:
                replay_evaluation = evaluate_replay_event(row)
                result = parse_trade_signal(row)
                logger.info(
                    "sample %d: payload=%s replay=%s result=%s",
                    processed,
                    row,
                    replay_evaluation,
                    repr(result),
                )
                if result:
                    accepted += 1
                elif failures_out:
                    try:
                        failures_out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
            except Exception as exc:
                logger.exception("error processing sample %d: %s", processed, exc)
            processed += 1
    else:
        p = Path(args.path)
        if not p.exists():
            print("file not found:", p)
            _release_lock(lock_path)
            return 2
        for row in read_jsonl(p):
            if args.limit >= 0 and processed >= args.limit:
                break
            try:
                replay_evaluation = evaluate_replay_event(row)
                result = parse_trade_signal(row)
                logger.info("row %d: replay=%s result=%s", processed, replay_evaluation, repr(result))
                if result:
                    accepted += 1
                elif failures_out:
                    try:
                        failures_out.write(json.dumps(row, ensure_ascii=False) + "\n")
                    except Exception:
                        pass
            except Exception as exc:
                logger.exception("error processing row %d: %s", processed, exc)
            processed += 1

    if failures_out:
        try:
            failures_out.flush()
            failures_out.close()
        except Exception:
            pass
    if have_lock:
        _release_lock(lock_path)

    print(f"processed={processed} accepted={accepted} trace=replay_trace.log")


if __name__ == '__main__':
    main()
