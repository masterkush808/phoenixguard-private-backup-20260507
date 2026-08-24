from __future__ import annotations
import pytest

import io
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, cast

import numpy as np
from fastapi.testclient import TestClient
from PIL import Image

from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.observer import SignalObserverService
from phoenixguard.mobile_api.service import MobileApiService
import phoenixguard.mobile_api.observer as observer_mod


def _build_best_play_analysis(
    service: SignalObserverService,
    result: Mapping[str, Any],
    *,
    render_config: Mapping[str, Any],
    file_path: str,
) -> dict[str, Any]:
    method = cast(
        Callable[..., dict[str, Any]],
        getattr(service, "_build_best_play_analysis"),
    )
    return method(result, render_config=render_config, file_path=file_path)


def _read_session(service: SignalObserverService, session_id: str) -> dict[str, Any]:
    method = cast(Callable[[str], dict[str, Any]], getattr(service, "_read_session"))
    return method(session_id)


def _write_session(service: SignalObserverService, session_id: str, payload: Mapping[str, Any]) -> None:
    method = cast(Callable[[str, Mapping[str, Any]], None], getattr(service, "_write_session"))
    method(session_id, payload)


def _build_signal_payload(
    service: SignalObserverService,
    session_payload: Mapping[str, Any],
    result: Mapping[str, Any],
    best_play: Mapping[str, Any],
    *,
    bundle_id: str,
    file_path: str,
) -> dict[str, Any]:
    method = cast(
        Callable[..., dict[str, Any]],
        getattr(service, "_build_signal_payload"),
    )
    return method(session_payload, result, best_play, bundle_id=bundle_id, file_path=file_path)


def _render_signal(
    service: SignalObserverService,
    signal: Mapping[str, Any],
    policy: Mapping[str, Any],
) -> dict[str, Any]:
    method = cast(
        Callable[[Mapping[str, Any], Mapping[str, Any]], dict[str, Any]],
        getattr(service, "_render_signal"),
    )
    return method(signal, policy)


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (160, 96), color=color).save(buf, format="PNG")
    return buf.getvalue()


def _quartet_uploads() -> list[tuple[str, bytes]]:
    return [
        ("higher_1.png", _png_bytes((30, 40, 50))),
        ("higher_2.png", _png_bytes((40, 50, 60))),
        ("lower_1.png", _png_bytes((50, 60, 70))),
        ("lower_2.png", _png_bytes((60, 70, 80))),
    ]


def _observer_result(
    *,
    action: str,
    confidence: float,
    gate_state: str = "confirmed",
    timing_state: str = "READY",
    execution_permission: str = "EXECUTE",
) -> dict[str, Any]:
    normalized_action = action.upper()
    return {
        "action": normalized_action,
        "headline_action": normalized_action,
        "execution_action": normalized_action if execution_permission == "EXECUTE" else "HOLD",
        "execution_confidence": confidence,
        "confidence": confidence,
        "decision_state": "CONFIRMED" if execution_permission == "EXECUTE" else "UNCERTAIN",
        "execution_permission": execution_permission,
        "memory_similarity": 0.48,
        "projection": {"direction": normalized_action},
        "timestamp": "2026-04-13T12:00:00+00:00",
        "timing_signal": {
            "entry_state": timing_state,
            "timing_score": 0.86 if timing_state == "READY" else 0.34,
        },
        "multi_timeframe": {
            "gate_state": gate_state,
            "gate_strength": 0.78 if gate_state == "confirmed" else 0.35,
            "aligned": gate_state == "confirmed",
            "summary": f"{normalized_action} structure",
            "entries": [],
        },
    }


class _FakeObserverPipelineAdapter:
    def __init__(self, root: Path, results: Sequence[Mapping[str, Any]]) -> None:
        self.root = root
        self.results = [dict(item) for item in results]
        self.calls = 0
        self.module: object = self

    def _build_best_play_input_snapshot(
        self,
        result: Mapping[str, Any],
        *,
        render_config: Mapping[str, Any],
        file_path: str,
    ) -> dict[str, Any]:
        _ = render_config
        action = str(result.get("execution_action", result.get("action", "HOLD")) or "HOLD").upper()
        confidence = float(result.get("execution_confidence", result.get("confidence", 0.0)) or 0.0)
        return {
            "combined": {
                "action": action,
                "execution_action": action,
                "confidence": confidence,
                "file_path": file_path,
            },
            "frames": [],
        }

    def describe(self) -> dict[str, Any]:
        return {
            "required_uploads": 4,
            "upload_order": [
                {"key": "higher_zoomed_out", "label": "Higher TF / Zoomed Out"},
                {"key": "higher_zoomed_in", "label": "Higher TF / Zoomed In"},
                {"key": "lower_zoomed_out", "label": "Lower TF / Zoomed Out"},
                {"key": "lower_zoomed_in", "label": "Lower TF / Zoomed In"},
            ],
            "timeframe_choices": ["M5", "M15"],
            "overlay_choices": ["history-plus-projection"],
            "council_scope_choices": ["standard"],
            "default_settings": {
                "overlay_mode": "history-plus-projection",
                "min_conf_global": 0.42,
                "min_conf_latest": 0.50,
                "history_depth": 8,
                "label_density": 10,
                "projection_focus": 0.35,
                "debug_depth": 6,
                "fuse_timeframe_overlays": False,
                "higher_timeframe": "M15",
                "lower_timeframe": "M5",
                "council_scope": "standard",
            },
        }

    def normalize_render_config(self, settings: Mapping[str, Any] | None) -> dict[str, Any]:
        payload = dict(self.describe()["default_settings"])
        payload.update(dict(settings or {}))
        return payload

    def analyze_bundle(
        self,
        upload_paths: Sequence[str],
        render_config: Mapping[str, Any],
    ) -> tuple[dict[str, Any], Any, str]:
        _ = render_config
        index = min(self.calls, len(self.results) - 1)
        self.calls += 1
        return dict(self.results[index]), np.zeros((96, 160, 3), dtype=np.uint8), str(upload_paths[-1])

    def normalize_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        return dict(result)

    def export_artifacts(
        self,
        result: Mapping[str, Any],
        source_image_state: Any,
        artifact_dir: Path,
        job_id: str,
    ) -> list[dict[str, Any]]:
        _ = result, source_image_state, job_id
        artifact_dir.mkdir(parents=True, exist_ok=True)
        target_path = artifact_dir / "observer_overlay.png"
        Image.new("RGB", (180, 100), color=(90, 110, 70)).save(target_path)
        return [
            {
                "name": target_path.name,
                "kind": "overlay",
                "label": "Observer Overlay",
                "path": str(target_path),
            }
        ]


def _fake_best_play_analysis(snapshot: Mapping[str, Any] | None) -> dict[str, Any]:
    combined = dict((snapshot or {}).get("combined", {}))
    action = str(combined.get("execution_action", combined.get("action", "HOLD")) or "HOLD").upper()
    if action not in {"BUY", "SELL"}:
        action = "HOLD"
    confidence = float(combined.get("confidence", 0.0) or 0.0)
    return {
        "status": "ready",
        "recommended_direction": action,
        "recommended_confidence": confidence,
        "recommended_risk": max(0.0, 1.0 - confidence),
        "recommended_play": f"{action} test play" if action in {"BUY", "SELL"} else "Stand Aside",
        "recommended_reasons": [f"{action} model fixture"],
        "likelihoods": {
            "BUY": confidence if action == "BUY" else 0.0,
            "SELL": confidence if action == "SELL" else 0.0,
            "HOLD": 1.0 if action == "HOLD" else max(0.0, 1.0 - confidence),
        },
        "frame_count": 0,
    }


def test_observer_best_play_analysis_does_not_synthesize_direction_without_snapshot(tmp_path: Path) -> None:
    adapter = _FakeObserverPipelineAdapter(tmp_path, [])
    adapter.module = object()
    service = SignalObserverService(root_dir=tmp_path / "observer_no_snapshot", pipeline_adapter=adapter)

    best_play = _build_best_play_analysis(
        service,
        _observer_result(action="BUY", confidence=0.92),
        render_config={},
        file_path="missing.png",
    )

    assert best_play["status"] == "unavailable"
    assert best_play["recommended_direction"] == "HOLD"


def test_observer_service_emits_entry_and_reverse_signals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(observer_mod, "analyze_best_play", _fake_best_play_analysis)
    adapter = _FakeObserverPipelineAdapter(
        tmp_path,
        [
            _observer_result(action="BUY", confidence=0.83),
            _observer_result(action="SELL", confidence=0.86),
        ],
    )
    service = SignalObserverService(
        root_dir=tmp_path / "observer",
        pipeline_adapter=adapter,
    )
    session = service.create_session(
        name="eurusd-fast",
        market="EURUSD",
        policy={
            "min_actionable_confidence": 0.55,
            "signal_cooldown_sec": 0.0,
        },
    )

    first = service.submit_bundle(str(session["session_id"]), _quartet_uploads())
    first_done = service.wait_for_bundle(str(session["session_id"]), str(first["bundle_id"]), timeout=5)
    assert first_done["signal"]["action"] == "BUY"
    assert first_done["signal"]["transition"] == "enter"
    assert first_done["signal"]["alert"] is True
    assert first_done["signal"]["thesis_action"] == "BUY"
    assert first_done["signal"]["market_phase"] in {"continuation", "pullback", "reversal", "consolidation", "transition"}

    second = service.submit_bundle(str(session["session_id"]), _quartet_uploads())
    second_done = service.wait_for_bundle(str(session["session_id"]), str(second["bundle_id"]), timeout=5)
    assert second_done["signal"]["action"] == "SELL"
    assert second_done["signal"]["transition"] == "reverse"
    assert second_done["signal"]["alert"] is True
    assert second_done["signal"]["thesis_action"] == "SELL"

    latest = service.latest_signal(str(session["session_id"]))
    assert latest["action"] == "SELL"
    assert latest["stale"] is False
    assert latest["status"] in {"ready", "watch"}


def test_observer_latest_signal_turns_hold_when_stale(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(observer_mod, "analyze_best_play", _fake_best_play_analysis)
    adapter = _FakeObserverPipelineAdapter(
        tmp_path,
        [_observer_result(action="BUY", confidence=0.81)],
    )
    service = SignalObserverService(
        root_dir=tmp_path / "observer_stale",
        pipeline_adapter=adapter,
    )
    session = service.create_session(
        name="stale-check",
        market="GBPUSD",
        policy={
            "min_actionable_confidence": 0.55,
            "min_freshness_score": 0.70,
            "freshness_half_life_sec": 2.0,
            "stale_after_sec": 4.0,
            "signal_cooldown_sec": 0.0,
        },
    )

    bundle = service.submit_bundle(str(session["session_id"]), _quartet_uploads())
    done = service.wait_for_bundle(str(session["session_id"]), str(bundle["bundle_id"]), timeout=5)
    completed_epoch = float(done["signal"]["completed_epoch"])

    monkeypatch.setattr(observer_mod.time, "time", lambda: completed_epoch + 10.0)
    stale = service.latest_signal(str(session["session_id"]))
    assert stale["stale"] is True
    assert stale["action"] == "HOLD"
    assert float(stale["effective_confidence"]) < float(done["signal"]["candidate_confidence"])


def test_observer_latest_signal_blocks_manual_test_signal(tmp_path: Path) -> None:
    service = SignalObserverService(
        root_dir=tmp_path / "observer_manual_test",
        pipeline_adapter=_FakeObserverPipelineAdapter(tmp_path, []),
    )
    session = service.create_session(
        name="manual-test-check",
        market="EURUSD",
        policy={"min_actionable_confidence": 0.20, "stale_after_sec": 60.0},
    )
    session_id = str(session["session_id"])
    payload = _read_session(service, session_id)
    payload["latest_signal"] = {
        "signal_id": "manual_test_20260516_120000",
        "action": "BUY",
        "base_action": "BUY",
        "candidate_action": "BUY",
        "execution_action": "BUY",
        "candidate_confidence": 0.80,
        "execution_confidence": 0.80,
        "actionable": True,
        "signal_armed": True,
        "expiry_seconds": 30,
        "timestamp": "2026-05-16T12:00:00+00:00",
        "test_mode": True,
        "decision_kernel": {"dominant_side": "BUY"},
    }
    _write_session(service, session_id, payload)

    latest = service.latest_signal(session_id)

    assert latest["status"] == "blocked"
    assert latest["action"] == "HOLD"
    assert latest["execution_action"] == "HOLD"
    assert latest["actionable"] is False
    assert latest["signal_armed"] is False
    assert latest["stale"] is True


def test_observer_latest_signal_ages_iso_timestamp_without_completed_epoch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    service = SignalObserverService(
        root_dir=tmp_path / "observer_iso_stale",
        pipeline_adapter=_FakeObserverPipelineAdapter(tmp_path, []),
    )
    session = service.create_session(
        name="iso-stale-check",
        market="EURUSD",
        policy={"min_actionable_confidence": 0.20, "stale_after_sec": 10.0},
    )
    session_id = str(session["session_id"])
    payload = _read_session(service, session_id)
    payload["latest_signal"] = {
        "signal_id": "real-but-old",
        "action": "BUY",
        "base_action": "BUY",
        "candidate_action": "BUY",
        "execution_action": "BUY",
        "candidate_confidence": 0.80,
        "execution_confidence": 0.80,
        "actionable": True,
        "signal_armed": True,
        "expiry_seconds": 30,
        "timestamp": "2026-05-16T12:00:00+00:00",
        "decision_kernel": {"dominant_side": "BUY"},
    }
    _write_session(service, session_id, payload)
    monkeypatch.setattr(observer_mod.time, "time", lambda: 1778932860.0)

    latest = service.latest_signal(session_id)

    assert latest["status"] == "stale"
    assert latest["action"] == "HOLD"
    assert latest["execution_action"] == "HOLD"
    assert latest["actionable"] is False


def test_observer_signal_policy_promotes_directional_watch_without_execute_permission(tmp_path: Path) -> None:
    service = SignalObserverService(
        root_dir=tmp_path / "observer_policy_watch",
        pipeline_adapter=_FakeObserverPipelineAdapter(tmp_path, []),
    )
    policy = {
        "min_actionable_confidence": 0.55,
        "min_directional_confidence": 0.46,
        "signal_cooldown_sec": 0.0,
    }
    session_payload: dict[str, Any] = {
        "market": "EURUSD",
        "policy": policy,
        "signal_history": [],
        "latest_signal": {},
    }
    result = _observer_result(
        action="BUY",
        confidence=0.84,
        gate_state="confirmed",
        timing_state="READY",
        execution_permission="WAIT_FOR_CONFIRMATION",
    )
    best_play: dict[str, Any] = {
        "recommended_direction": "BUY",
        "recommended_confidence": 0.76,
        "recommended_risk": 0.12,
        "recommended_play": "BUY Pullback Continuation",
        "recommended_reasons": ["higher bias BUY"],
        "likelihoods": {"BUY": 0.76, "SELL": 0.14, "HOLD": 0.10},
    }

    signal = _build_signal_payload(
        service,
        session_payload,
        result,
        best_play,
        bundle_id="bundle-watch",
        file_path="watch.png",
    )
    rendered = _render_signal(service, signal, policy)

    assert signal["actionable"] is False
    assert signal["directional_watch_ready"] is True
    assert signal["base_action"] == "BUY"
    assert rendered["action"] == "BUY"
    assert rendered["status"] == "watch"
    assert "directional bias active while execute gate waits" in rendered["reasons"]


def test_observer_single_surface_mode_arms_signal_before_execute_permission(tmp_path: Path) -> None:
    service = SignalObserverService(
        root_dir=tmp_path / "observer_arm_state",
        pipeline_adapter=_FakeObserverPipelineAdapter(tmp_path, []),
    )
    policy: dict[str, Any] = {
        "single_surface_mode": True,
        "min_actionable_confidence": 0.55,
        "min_directional_confidence": 0.44,
        "signal_cooldown_sec": 0.0,
    }
    session_payload: dict[str, Any] = {
        "market": "EURUSD",
        "policy": policy,
        "signal_history": [],
        "latest_signal": {},
    }
    result = _observer_result(
        action="BUY",
        confidence=0.84,
        gate_state="watch",
        timing_state="READY",
        execution_permission="WAIT_FOR_CONFIRMATION",
    )
    best_play: dict[str, Any] = {
        "recommended_direction": "BUY",
        "recommended_confidence": 0.79,
        "recommended_risk": 0.11,
        "recommended_play": "BUY continuation setup",
        "recommended_reasons": ["continuation structure"],
        "likelihoods": {"BUY": 0.79, "SELL": 0.11, "HOLD": 0.10},
    }

    signal = _build_signal_payload(
        service,
        session_payload,
        result,
        best_play,
        bundle_id="bundle-arm",
        file_path="arm.png",
    )
    rendered = _render_signal(service, signal, policy)

    assert signal["actionable"] is True
    assert signal["signal_armed"] is True
    assert signal["signal_armed_action"] == "BUY"
    assert signal["base_action"] == "BUY"
    assert rendered["action"] == "BUY"
    assert rendered["status"] in {"armed", "ready"}


def test_observer_single_surface_arming_rejects_flip_flop_reversal_noise(tmp_path: Path) -> None:
    service = SignalObserverService(
        root_dir=tmp_path / "observer_flip_guard",
        pipeline_adapter=_FakeObserverPipelineAdapter(tmp_path, []),
    )
    policy: dict[str, Any] = {
        "single_surface_mode": True,
        "min_actionable_confidence": 0.55,
        "min_directional_confidence": 0.44,
        "signal_cooldown_sec": 0.0,
    }
    history = [
        {"base_action": "BUY", "candidate_action": "BUY"},
        {"base_action": "SELL", "candidate_action": "SELL"},
        {"base_action": "BUY", "candidate_action": "BUY"},
        {"base_action": "SELL", "candidate_action": "SELL"},
        {"base_action": "BUY", "candidate_action": "BUY"},
    ]
    session_payload: dict[str, Any] = {
        "market": "EURUSD",
        "policy": policy,
        "signal_history": history,
        "latest_signal": {"base_action": "BUY", "action": "BUY", "candidate_action": "BUY"},
    }
    result = _observer_result(
        action="SELL",
        confidence=0.58,
        gate_state="watch",
        timing_state="READY",
        execution_permission="WAIT_FOR_CONFIRMATION",
    )
    result["chart_state"] = {"reversal_probability": 0.08}
    result["sequence_state"] = {"reversal_probability": 0.10}
    best_play: dict[str, Any] = {
        "recommended_direction": "SELL",
        "recommended_confidence": 0.54,
        "recommended_risk": 0.20,
        "recommended_play": "SELL drift",
        "recommended_reasons": ["counter pressure"],
        "likelihoods": {"BUY": 0.24, "SELL": 0.54, "HOLD": 0.22},
    }

    signal = _build_signal_payload(
        service,
        session_payload,
        result,
        best_play,
        bundle_id="bundle-flip",
        file_path="flip.png",
    )
    rendered = _render_signal(service, signal, policy)

    assert signal["signal_armed"] is False
    assert signal["signal_armed_reverse_guard"] is False
    assert rendered["action"] == "HOLD"
    assert rendered["status"] in {"hold", "watch"}


def test_observer_signal_policy_keeps_hold_when_gate_is_blocked(tmp_path: Path) -> None:
    service = SignalObserverService(
        root_dir=tmp_path / "observer_policy_hold",
        pipeline_adapter=_FakeObserverPipelineAdapter(tmp_path, []),
    )
    policy = {
        "min_actionable_confidence": 0.55,
        "min_directional_confidence": 0.46,
        "signal_cooldown_sec": 0.0,
    }
    session_payload: dict[str, Any] = {
        "market": "EURUSD",
        "policy": policy,
        "signal_history": [],
        "latest_signal": {},
    }
    result = _observer_result(
        action="SELL",
        confidence=0.86,
        gate_state="blocked",
        timing_state="READY",
        execution_permission="WAIT_FOR_CONFIRMATION",
    )
    best_play: dict[str, Any] = {
        "recommended_direction": "SELL",
        "recommended_confidence": 0.80,
        "recommended_risk": 0.14,
        "recommended_play": "SELL Reversal Fade",
        "recommended_reasons": ["risk still elevated"],
        "likelihoods": {"BUY": 0.10, "SELL": 0.80, "HOLD": 0.10},
    }

    signal = _build_signal_payload(
        service,
        session_payload,
        result,
        best_play,
        bundle_id="bundle-hold",
        file_path="hold.png",
    )
    rendered = _render_signal(service, signal, policy)

    assert signal["actionable"] is False
    assert signal["directional_watch_ready"] is False
    assert signal["base_action"] == "HOLD"
    assert rendered["action"] == "HOLD"


def test_observer_http_surface_creates_session_and_returns_latest_signal(tmp_path: Path) -> None:
    adapter = _FakeObserverPipelineAdapter(
        tmp_path,
        [_observer_result(action="BUY", confidence=0.84)],
    )
    mobile_service = MobileApiService(
        root_dir=tmp_path / "mobile_api",
        pipeline_adapter=adapter,
    )
    observer_service = SignalObserverService(
        root_dir=tmp_path / "observer_api",
        pipeline_adapter=adapter,
    )
    client: Any = TestClient(create_app(service=mobile_service, observer_service=observer_service))

    create_response: Any = client.post(
        "/v1/mobile/observer/sessions",
        json={
            "name": "eurusd-observer",
            "market": "EURUSD",
            "policy": {
                "min_actionable_confidence": 0.55,
                "signal_cooldown_sec": 0.0,
            },
        },
    )
    assert create_response.status_code == 201
    session_id = create_response.json()["session_id"]

    submit_response: Any = client.post(
        f"/v1/mobile/observer/sessions/{session_id}/bundles",
        files=[
            ("screenshots", ("higher_1.png", _png_bytes((10, 20, 30)), "image/png")),
            ("screenshots", ("higher_2.png", _png_bytes((20, 30, 40)), "image/png")),
            ("screenshots", ("lower_1.png", _png_bytes((30, 40, 50)), "image/png")),
            ("screenshots", ("lower_2.png", _png_bytes((40, 50, 60)), "image/png")),
        ],
    )
    assert submit_response.status_code == 202
    bundle_id = submit_response.json()["bundle_id"]
    observer_service.wait_for_bundle(session_id, bundle_id, timeout=5)

    latest_signal: Any = client.get(f"/v1/mobile/observer/sessions/{session_id}/signals/latest")
    assert latest_signal.status_code == 200
    assert latest_signal.json()["action"] == "BUY"
