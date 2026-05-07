from __future__ import annotations

import json
import sys
from pathlib import Path


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

import main


def _restore_capture_state(snapshot: dict[str, object]) -> None:
    with main._capture_runtime_lock:
        main._capture_runtime_state.clear()
        main._capture_runtime_state.update(snapshot)


def test_restore_capture_recovery_state_restores_pending_bundle(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)
    capture_file = tmp_path / "recovered_capture.png"
    capture_file.write_bytes(b"capture")

    monkeypatch.setattr(main.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(main.RUNTIME, "logs_dir", logs_dir)

    original_state = dict(main._capture_runtime_state)
    try:
        with main._capture_runtime_lock:
            main._capture_runtime_state["pending_bundle"] = []
            main._capture_runtime_state["inflight_bundle"] = []
            main._capture_runtime_state["bundle_size"] = 4
            main._capture_runtime_state["status"] = "Hotkey capture offline."

        recovery_payload = {
            "pending_bundle": [
                {
                    "file_path": str(capture_file),
                    "captured_at": "2026-03-28T00:00:00+00:00",
                    "slot_index": 1,
                }
            ],
            "inflight_bundle": [],
            "bundle_size": 4,
            "requested_hotkey": "F4",
            "active_hotkey": "F4",
            "bundle_started_at": "2026-03-28T00:00:00+00:00",
            "bundle_started_epoch": 123.0,
            "last_capture_file": capture_file.name,
            "last_capture_time": "2026-03-28T00:00:00+00:00",
        }
        (data_dir / "capture_recovery_state.json").write_text(json.dumps(recovery_payload), encoding="utf-8")

        restored = main._restore_capture_recovery_state()
        snapshot = main._get_capture_runtime_snapshot()

        assert restored["pending_bundle_count"] == 1
        assert snapshot["pending_bundle_count"] == 1
        assert snapshot["requested_hotkey"] == "Ctrl+V"
        assert snapshot["active_hotkey"] == "Ctrl+V"
        assert "Recovered 1/4 hotkey capture" in snapshot["status"]
    finally:
        _restore_capture_state(original_state)


def test_resume_recovered_capture_bundle_submits_background_work(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(main.RUNTIME, "logs_dir", logs_dir)

    original_state = dict(main._capture_runtime_state)
    calls: list[tuple[list[dict[str, object]], str]] = []

    class _ImmediateExecutor:
        def submit(self, fn, *args, **kwargs):  # type: ignore[no-untyped-def]
            fn(*args, **kwargs)
            return None

    try:
        with main._capture_runtime_lock:
            main._capture_runtime_state["inflight_bundle"] = [
                {
                    "file_path": str(tmp_path / "bundle_a.png"),
                    "captured_at": "2026-03-28T00:00:00+00:00",
                    "slot_index": 1,
                }
            ]
            main._capture_runtime_state["inflight_source"] = "recovered"

        monkeypatch.setattr(main, "_get_background_executor", lambda: _ImmediateExecutor())
        monkeypatch.setattr(
            main,
            "_process_multi_timeframe_bundle",
            lambda bundle, source="hotkey": calls.append((bundle, source)) or True,
        )

        main._resume_recovered_capture_bundle_if_needed()

        assert calls
        assert calls[0][1] == "recovered"
    finally:
        _restore_capture_state(original_state)


def test_record_runtime_crash_writes_journal_entry(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    logs_dir = tmp_path / "logs"
    data_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(main.RUNTIME, "data_dir", data_dir)
    monkeypatch.setattr(main.RUNTIME, "logs_dir", logs_dir)

    main._record_runtime_crash(scope="unit-test", error="boom", traceback_text="traceback")

    rows = [
        json.loads(line)
        for line in (logs_dir / "runtime_crash_journal.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows
    assert rows[-1]["scope"] == "unit-test"
    assert rows[-1]["error"] == "boom"


def test_get_local_ensemble_returns_cached_runtime_without_runtime_type_name(monkeypatch) -> None:
    cache_key = main._local_ensemble_cache_key(target_models=["simclr"], max_loaded_models=1)
    sentinel = object()
    original_cache = dict(main._local_ensemble_cache)
    original_future_cache = dict(main._local_ensemble_future_cache)
    original_error_cache = dict(main._local_ensemble_error_cache)

    try:
        monkeypatch.setattr(main.RUNTIME, "enable_local_ensemble", True)
        with main._local_ensemble_lock:
            main._local_ensemble_cache.clear()
            main._local_ensemble_cache[cache_key] = sentinel
            main._local_ensemble_future_cache.clear()
            main._local_ensemble_error_cache.clear()

        runtime = main._get_local_ensemble(
            block=True,
            target_models=["simclr"],
            max_loaded_models=1,
        )

        assert runtime is sentinel
    finally:
        with main._local_ensemble_lock:
            main._local_ensemble_cache.clear()
            main._local_ensemble_cache.update(original_cache)
            main._local_ensemble_future_cache.clear()
            main._local_ensemble_future_cache.update(original_future_cache)
            main._local_ensemble_error_cache.clear()
            main._local_ensemble_error_cache.update(original_error_cache)


def test_should_force_side_effect_free_council_when_saved_bundles_exist(monkeypatch) -> None:
    monkeypatch.setattr(main, "_saved_local_ensemble_artifacts_available", lambda target_models=None: True)

    assert main._should_force_side_effect_free_council(
        side_effect_free=True,
        use_local_ensemble=None,
        council_scope="auto",
        target_models=None,
    ) is True
    assert main._should_force_side_effect_free_council(
        side_effect_free=True,
        use_local_ensemble=True,
        council_scope="half",
        target_models=["simclr"],
    ) is True
    assert main._should_force_side_effect_free_council(
        side_effect_free=True,
        use_local_ensemble=False,
        council_scope="auto",
        target_models=None,
    ) is False
    assert main._should_force_side_effect_free_council(
        side_effect_free=False,
        use_local_ensemble=None,
        council_scope="auto",
        target_models=None,
    ) is False
    assert main._should_force_side_effect_free_council(
        side_effect_free=True,
        use_local_ensemble=None,
        council_scope="off",
        target_models=None,
    ) is False


def test_get_local_ensemble_allow_when_disabled_returns_cached_runtime(monkeypatch) -> None:
    cache_key = main._local_ensemble_cache_key(target_models=["simclr"], max_loaded_models=1)
    sentinel = object()
    original_cache = dict(main._local_ensemble_cache)
    original_future_cache = dict(main._local_ensemble_future_cache)
    original_error_cache = dict(main._local_ensemble_error_cache)

    try:
        monkeypatch.setattr(main.RUNTIME, "enable_local_ensemble", False)
        with main._local_ensemble_lock:
            main._local_ensemble_cache.clear()
            main._local_ensemble_cache[cache_key] = sentinel
            main._local_ensemble_future_cache.clear()
            main._local_ensemble_error_cache.clear()

        runtime = main._get_local_ensemble(
            block=True,
            target_models=["simclr"],
            max_loaded_models=1,
            allow_when_disabled=True,
        )

        assert runtime is sentinel
        assert (
            main._get_local_ensemble(
                block=True,
                target_models=["simclr"],
                max_loaded_models=1,
                allow_when_disabled=False,
            )
            is None
        )
    finally:
        with main._local_ensemble_lock:
            main._local_ensemble_cache.clear()
            main._local_ensemble_cache.update(original_cache)
            main._local_ensemble_future_cache.clear()
            main._local_ensemble_future_cache.update(original_future_cache)
            main._local_ensemble_error_cache.clear()
            main._local_ensemble_error_cache.update(original_error_cache)


def test_sync_forecast_into_chart_state_promotes_forecast_state_and_projection() -> None:
    chart_state = {
        "entry_type": "continuation",
        "structure_setup": "none",
        "structure_trade_ready": False,
        "structure_setup_source": "sequence",
        "projection_bias_direction": "BUY",
        "projection_bias_confidence": 0.31,
        "projection_dominance": 0.02,
        "projection_explanation": "old explanation",
        "projected_next_box": {
            "box_type": "balance",
            "direction": "BUY",
            "confidence": 0.31,
            "dominance_gap": 0.02,
            "explanation": "old explanation",
            "path_clarity": 0.61,
        },
    }
    forecast = {
        "structure_setup": "reversal_release",
        "structure_trade_ready": 1.0,
        "projected_box_type": "reversal_base",
        "projected_box_direction": "SELL",
        "projected_box_confidence": 0.78,
        "projection_bias_confidence": 0.74,
        "projection_dominance": 0.16,
        "projected_box_explanation": "counter-macro sell release",
    }

    synced = main._sync_forecast_into_chart_state(chart_state, forecast)

    assert synced["structure_setup"] == "reversal_release"
    assert synced["structure_trade_ready"] is True
    assert synced["structure_setup_source"] == "forecast"
    assert synced["entry_type"] == "reversal"
    assert synced["projection_bias_direction"] == "SELL"
    assert synced["projection_bias_confidence"] == 0.74
    assert synced["projection_dominance"] == 0.16
    assert synced["projection_explanation"] == "counter-macro sell release"
    assert synced["projected_next_box"]["box_type"] == "reversal_base"
    assert synced["projected_next_box"]["direction"] == "SELL"
    assert synced["projected_next_box"]["confidence"] == 0.78
    assert synced["projected_next_box"]["dominance_gap"] == 0.16
    assert synced["projected_next_box"]["path_clarity"] == 0.61


def test_sync_forecast_into_chart_state_preserves_existing_ready_source_when_unchanged() -> None:
    chart_state = {
        "entry_type": "continuation",
        "structure_setup": "impulse_chain",
        "structure_trade_ready": True,
        "structure_setup_source": "council",
        "projected_next_box": {
            "box_type": "pullback",
            "direction": "SELL",
            "confidence": 0.72,
            "dominance_gap": 0.11,
        },
    }
    forecast = {
        "structure_setup": "impulse_chain",
        "structure_trade_ready": 1.0,
        "projected_box_type": "pullback",
        "projected_box_direction": "SELL",
        "projected_box_confidence": 0.72,
        "projection_bias_confidence": 0.69,
        "projection_dominance": 0.11,
    }

    synced = main._sync_forecast_into_chart_state(chart_state, forecast)

    assert synced["structure_setup"] == "impulse_chain"
    assert synced["structure_setup_source"] == "council"
