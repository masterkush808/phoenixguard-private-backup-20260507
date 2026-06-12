from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import time
from typing import Any, Mapping


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _state(result: Mapping[str, Any]) -> str:
    council = _mapping(result.get("model_council"))
    execution = _mapping(result.get("execution"))
    return str(council.get("final_state") or execution.get("state") or result.get("final_state") or "WATCHING").upper()


def _side(result: Mapping[str, Any]) -> str:
    council = _mapping(result.get("model_council"))
    execution = _mapping(result.get("execution"))
    return str(execution.get("side") or council.get("final_side") or result.get("side") or "HOLD").upper()


def _expected(expected: Mapping[str, Any], key: str) -> Any:
    nested = _mapping(expected.get("expected"))
    return expected.get(key, nested.get(key))


@dataclass
class ReplayMetricsRecorder:
    simulation_id: str
    scenario_name: str = ""
    started_epoch: float = field(default_factory=time.time)
    frames_processed: int = 0
    paper_entries: list[dict[str, Any]] = field(default_factory=list)
    blocked_entries: int = 0
    avoided_bad_trades: int = 0
    late_chase_avoidance_count: int = 0
    opposing_force_avoidance_count: int = 0
    false_executable_count: int = 0
    missed_good_entry_count: int = 0
    entry_quality_distribution: Counter[str] = field(default_factory=Counter)
    council_disagreement_distribution: Counter[str] = field(default_factory=Counter)
    overlay_accuracy_metrics: list[dict[str, Any]] = field(default_factory=list)
    latency_ms: list[float] = field(default_factory=list)

    def record_frame(
        self,
        *,
        packet: Mapping[str, Any],
        council_result: Mapping[str, Any],
        paper_result: Mapping[str, Any] | None = None,
        overlay_metrics: Mapping[str, Any] | None = None,
        latency_ms: float = 0.0,
    ) -> None:
        self.frames_processed += 1
        expected = _mapping(packet.get("expected"))
        state = _state(council_result)
        executable = state.endswith("EXECUTABLE") or _mapping(council_result.get("execution")).get("enabled") is True
        expected_state = str(_expected(expected, "execution_state") or "").upper()
        expected_quality = str(_expected(expected, "entry_quality") or "UNKNOWN").upper()
        expected_trap = str(_expected(expected, "trap") or "").upper()
        if expected_quality:
            self.entry_quality_distribution[expected_quality] += 1
        disagreement = _float(_mapping(council_result.get("model_council")).get("disagreement_score"), 0.0)
        bucket = "LOW" if disagreement < 0.35 else "MEDIUM" if disagreement < 0.65 else "HIGH"
        self.council_disagreement_distribution[bucket] += 1
        if latency_ms:
            self.latency_ms.append(float(latency_ms))
        if overlay_metrics:
            self.overlay_accuracy_metrics.append(dict(overlay_metrics))
        if paper_result and paper_result.get("recorded") is not False:
            self.paper_entries.append(dict(paper_result))
        if not executable:
            self.blocked_entries += 1
        if expected_trap and not executable:
            self.avoided_bad_trades += 1
            if "LATE_CHASE" in expected_trap or "IMPULSE" in expected_trap:
                self.late_chase_avoidance_count += 1
            if "OPPOSING" in expected_trap or "SUPPLY" in expected_trap or "DEMAND" in expected_trap:
                self.opposing_force_avoidance_count += 1
        if executable and expected_state in {"WATCHING", "BLOCKED", "NO_EXECUTION"}:
            self.false_executable_count += 1
        if not executable and expected_state in {"EXECUTABLE", "BUY_EXECUTABLE", "SELL_EXECUTABLE"}:
            self.missed_good_entry_count += 1

    def summary(self) -> dict[str, Any]:
        mfe_values = [_float(_mapping(row.get("outcome_metrics") or row.get("metrics") or row.get("outcome")).get("mfe"), 0.0) for row in self.paper_entries]
        mae_values = [_float(_mapping(row.get("outcome_metrics") or row.get("metrics") or row.get("outcome")).get("mae"), 0.0) for row in self.paper_entries]
        average_mfe = sum(mfe_values) / max(1, len(mfe_values))
        average_mae = sum(mae_values) / max(1, len(mae_values))
        win_loss: Counter[str] = Counter()
        for row in self.paper_entries:
            metrics = _mapping(row.get("outcome_metrics") or row.get("metrics") or row.get("outcome"))
            setup = str(row.get("entry_angle_class") or row.get("setup") or "UNKNOWN")
            outcome = str(metrics.get("final_outcome_proxy") or "UNKNOWN")
            win_loss[f"{setup}:{outcome}"] += 1
        return {
            "simulation_id": self.simulation_id,
            "scenario_name": self.scenario_name,
            "frames_processed": self.frames_processed,
            "paper_entries": len(self.paper_entries),
            "blocked_entries": self.blocked_entries,
            "avoided_bad_trades": self.avoided_bad_trades,
            "late_chase_avoidance_count": self.late_chase_avoidance_count,
            "opposing_force_avoidance_count": self.opposing_force_avoidance_count,
            "false_executable_count": self.false_executable_count,
            "missed_good_entry_count": self.missed_good_entry_count,
            "average_MFE": round(float(average_mfe), 8),
            "average_MAE": round(float(average_mae), 8),
            "MFE/MAE ratio": round(float(average_mfe / max(average_mae, 1e-9)), 4) if self.paper_entries else 0.0,
            "win/loss proxy by setup": dict(win_loss),
            "entry quality distribution": dict(self.entry_quality_distribution),
            "model council disagreement distribution": dict(self.council_disagreement_distribution),
            "overlay accuracy metrics": self.overlay_accuracy_metrics,
            "latency metrics": {
                "average_ms": round(sum(self.latency_ms) / max(1, len(self.latency_ms)), 3),
                "max_ms": round(max(self.latency_ms), 3) if self.latency_ms else 0.0,
            },
            "duration_seconds": round(float(time.time() - self.started_epoch), 6),
        }
