from __future__ import annotations

from pathlib import Path
import sys

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from tools.run_final_10h_production_certification import model_status_for_certification, source_lock_status_for_certification


def test_source_lock_status_accepts_live_state_identity_when_trace_is_slow() -> None:
    live: dict[str, object] = {
        "broker_source_lock_id": "source-lock-edge-broker",
        "chart_transform_id": "chart-transform-42",
        "wrong_surface": False,
    }

    status = source_lock_status_for_certification(live, {})

    assert status["valid"] is True
    assert status["status"] == "VALID"
    assert status["lock_id"] == "source-lock-edge-broker"
    assert status["reason"] == "live_state_source_lock_identity"


def test_model_status_accepts_seven_awake_models_without_runtime_trace_gate() -> None:
    performance: dict[str, object] = {
        "model_state": {
            "models_awake": 7,
            "models_total": 7,
        }
    }

    status = model_status_for_certification({}, performance, {})

    assert status["models_awake"] == 7
    assert status["models_total"] == 7
    assert status["all_required_models_awake"] is True
