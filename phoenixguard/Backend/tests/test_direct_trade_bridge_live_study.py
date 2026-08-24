import importlib.util
import json
import sys
import time
from types import SimpleNamespace
from pathlib import Path
from typing import Any, cast

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = PROJECT_ROOT / "Backend" / "launch" / "phoenixguard_direct_trade_bridge.py"
CALIBRATION_MODULE_PATH = PROJECT_ROOT / "Backend" / "launch" / "phoenixguard_trigger_calibration.py"


def _load_module(module_name: str, module_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_bridge_module(module_name: str = "phoenixguard_direct_trade_bridge"):
    return _load_module(module_name, MODULE_PATH)


def _load_calibration_module(module_name: str = "phoenixguard_trigger_calibration"):
    return _load_module(module_name, CALIBRATION_MODULE_PATH)


def _strategist_verdict_payload(**overrides: Any):
    latest_signal = {
        "published_epoch": time.time(),
        "signal_age_sec": 0.5,
        "freshness_window_sec": 300.0,
        "freshness_score": 1.0,
        "book_rule_action_signal_v3": {
            "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
            "status": "ROLE_FLIP_RETEST_READY",
            "action": "BUY",
            "watch_side": "BUY",
            "actionable": True,
            "confidence": 0.88,
            "playbook": "ROLE_FLIP_RETEST",
            "scenario": "Completed book-rule buy retest is aligned.",
            "trigger": "Enter on the completed retest candle.",
            "closed_candle_key": "closed-strategist",
            "closed_candle_sequence": 1,
            "pair": "USD/CAD OTC",
            "timeframe": "M5",
            "rule_traceability": {
                "selected_book_rule_ids": ["RULE_A", "RULE_B"],
            },
        },
    }
    latest_signal.update(overrides.get("latest_signal", {}))
    payload = {"session_id": "pocket-live-8788", "latest_signal": latest_signal}
    payload.update({k: v for k, v in overrides.items() if k != "latest_signal"})
    return payload


def test_bridge_default_source_is_strategist():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_strategist_default")
    assert bridge.DEFAULT_SIGNAL_SOURCE == "strategist"
    parsed = bridge._build_parser().parse_args([])
    assert parsed.signal_source == "strategist"


def test_bridge_accepts_strategist_verdict_by_default():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_strategist_accepts")
    payload = _strategist_verdict_payload()

    result = bridge._resolve_trade_payload(payload, score_threshold=0.0)
    assert result["side"] == "BUY"
    assert result["source"] == "direct_book_rule_signal"
    assert result["playbook"] == "ROLE_FLIP_RETEST"
    assert result["book_rule_ids"] == ["RULE_A", "RULE_B"]
    assert result["candle_key"] == "closed-strategist"


@pytest.mark.parametrize(
    "removed_source",
    ["bias", "hybrid", "high_frequency", "two-candle"],
)
def test_removed_sources_never_authorize_trades(removed_source: str):
    bridge = _load_bridge_module(f"phoenixguard_direct_trade_bridge_removed_{removed_source.replace('-', '_')}")
    payload = _strategist_verdict_payload()

    with pytest.raises(bridge.TradeRejected, match="No actionable live signal"):
        bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source=removed_source)


def test_hybrid_lane_never_authorizes_even_when_fast_lanes_look_ready():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_hybrid_demoted")
    payload = _strategist_verdict_payload(
        **{
            "latest_signal": {
                "entry_state": "TRIGGER_READY",
                "actionable": True,
                "action": "BUY",
                "timing_signal": {"entry_state": "TRIGGER_READY", "timing_score": 0.9},
                "two_candle_study": {
                    "status": "READY",
                    "primary_pressure": "SELL",
                    "confidence": 0.95,
                    "summary": "Two-candle continuation is leaning SELL.",
                },
                "book_rule_action_signal_v3": {
                    "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                    "status": "WAITING_FOR_CURRENT_BOOK_TRIGGER",
                    "action": "WAIT",
                    "watch_side": "BUY",
                    "actionable": False,
                    "confidence": 0.0,
                    "playbook": "STRUCTURE_CONTINUATION",
                    "closed_candle_key": "closed-demoted",
                    "closed_candle_sequence": 33,
                },
            }
        }
    )

    with pytest.raises(bridge.TradeRejected, match="No actionable live signal"):
        bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="hybrid")


def test_execution_timing_veto_blocks_actionable_verdict() -> None:
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_timing_veto")
    payload = {
        "session_id": "pocket-live-8788",
        "last_capture_epoch": time.time(),
        "latest_signal": {
            "published_epoch": time.time(),
            "execution_timing": {
                "entry_allowed": False,
                "block_reason": "BUY is already in the upper studied history area; wait for a lower support pullback.",
            },
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "STRATEGIST_ACTION_CONFIRMED",
                "action": "BUY",
                "watch_side": "BUY",
                "actionable": True,
                "playbook": "CANDLE_REVERSAL_AT_STRUCTURE",
                "closed_candle_key": "closed-veto",
                "closed_candle_sequence": 11,
            },
        },
    }

    with pytest.raises(bridge.TradeRejected, match="execution-timing veto") as rejection:
        bridge._resolve_trade_payload(payload)

    assert "upper studied history area" in str(rejection.value)


def test_pre_click_confirmation_rejects_side_and_candle_drift() -> None:
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_preclick")

    with pytest.raises(bridge.TradeRejected, match="decision was SELL"):
        bridge._confirm_pre_click_match(
            "SELL",
            "candle-a",
            {"side": "BUY", "candle_key": "candle-a"},
        )

    with pytest.raises(bridge.TradeRejected, match="newer candle") as drifted:
        bridge._confirm_pre_click_match(
            "SELL",
            "candle-a",
            {"side": "SELL", "candle_key": "candle-b"},
        )
    assert getattr(drifted.value, "quiet", False) is False

    # Matching side and candle passes silently.
    bridge._confirm_pre_click_match("SELL", "candle-a", {"side": "SELL", "candle_key": "candle-a"})


def test_stale_observation_state_is_refused_regardless_of_verdict() -> None:
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_state_age")
    stale = time.time() - 2 * 24 * 3600
    payload = {
        "session_id": "pocket-live-8788",
        "last_capture_epoch": stale,
        "latest_signal": {
            "published_epoch": stale,
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "BOOK_ACTION_CONFIRMED",
                "action": "SELL",
                "watch_side": "SELL",
                "actionable": True,
                "playbook": "STOP_HUNT_BMS_RTO",
                "closed_candle_key": "closed-stale",
                "closed_candle_sequence": 7,
            },
        },
    }

    with pytest.raises(bridge.TradeRejected, match="Observation state is stale"):
        bridge._resolve_trade_payload(payload)

    # Defense in depth: with the state-age gate disabled the expired valid_until
    # epoch still refuses a two-day-old verdict.
    bridge = cast(Any, bridge)
    bridge._max_state_age_seconds = 0.0
    try:
        with pytest.raises(bridge.TradeRejected, match="Live signal expired"):
            bridge._resolve_trade_payload(payload)
    finally:
        bridge._max_state_age_seconds = bridge.DEFAULT_MAX_STATE_AGE_SECONDS


def test_waiting_verdict_reason_names_what_the_strategist_published() -> None:
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_reason_detail")
    payload = {
        "session_id": "pocket-live-8788",
        "last_capture_epoch": time.time(),
        "latest_signal": {
            "published_epoch": time.time(),
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "BOOK_EVIDENCE_CONFLICT",
                "action": "WAIT",
                "watch_side": "SELL",
                "actionable": False,
                "playbook": "AMD_DISTRIBUTION",
                "closed_candle_key": "closed-gated",
                "closed_candle_sequence": 9,
            },
        },
    }

    with pytest.raises(bridge.TradeRejected) as rejection:
        bridge._resolve_trade_payload(payload)

    message = str(rejection.value)
    assert "No actionable live signal" in message
    assert "status=BOOK_EVIDENCE_CONFLICT" in message
    assert "playbook=AMD_DISTRIBUTION" in message
    assert "state_age=0s" in message


def test_superseded_and_partial_frames_are_skipped_quietly() -> None:
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_watermark")
    now = time.time()

    def payload(epoch: float, *, with_verdict: bool = True) -> dict[str, object]:
        book = (
            {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "BOOK_EVIDENCE_CONFLICT",
                "action": "WAIT",
                "actionable": False,
                "playbook": "AMD_DISTRIBUTION",
            }
            if with_verdict
            else {}
        )
        return {
            "session_id": "s",
            "last_capture_epoch": epoch,
            "latest_signal": {"published_epoch": epoch, "book_rule_action_signal_v3": book},
        }

    fresh = payload(now - 5)
    with pytest.raises(bridge.TradeRejected) as gated:
        bridge._resolve_trade_payload(fresh)
    assert getattr(gated.value, "quiet", False) is False

    with pytest.raises(bridge.TradeRejected) as superseded:
        bridge._resolve_trade_payload(payload(now - 90))
    assert getattr(superseded.value, "quiet", False) is True
    assert "Superseded" in str(superseded.value)

    with pytest.raises(bridge.TradeRejected) as partial:
        bridge._resolve_trade_payload(payload(now - 4, with_verdict=False))
    assert getattr(partial.value, "quiet", False) is True


def test_bridge_reports_staleness_before_a_waiting_strategist_market():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_stale_before_timing")
    now = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "last_capture_epoch": now - 45,
        "latest_signal": {
            "published_epoch": now - 40,
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "WAITING_FOR_CURRENT_BOOK_TRIGGER",
                "action": "WAIT",
                "watch_side": "BUY",
                "actionable": False,
                "closed_candle_key": "closed-stale-strategist",
                "closed_candle_sequence": 19,
            },
        },
    }

    # Staleness is evaluated on resolved signals; a waiting market still
    # rejects as "no actionable live signal" even when observations are old.
    with pytest.raises(bridge.TradeRejected, match="No actionable live signal"):
        bridge._resolve_trade_payload(
            payload,
            score_threshold=0.0,
            max_signal_age_seconds=15,
        )

    verdict_payload = {
        "session_id": "pocket-live-8788",
        "last_capture_epoch": now - 45,
        "latest_signal": {
            "published_epoch": now - 40,
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "ROLE_FLIP_RETEST_READY",
                "action": "BUY",
                "watch_side": "BUY",
                "actionable": True,
                "closed_candle_key": "closed-stale-strategist",
                "closed_candle_sequence": 19,
            },
        },
    }
    with pytest.raises(bridge.TradeRejected, match="Live signal stale"):
        bridge._resolve_trade_payload(
            verdict_payload,
            score_threshold=0.0,
            max_signal_age_seconds=15,
        )



def test_bridge_book_rules_mode_still_available_when_explicitly_requested():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_book_rules_mode")
    payload = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "published_epoch": time.time(),
            "signal_age_sec": 0.5,
            "freshness_window_sec": 300.0,
            "freshness_score": 1.0,
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "ROLE_FLIP_RETEST_READY",
                "action": "BUY",
                "watch_side": "BUY",
                "actionable": True,
                "confidence": 0.88,
                "playbook": "ROLE_FLIP_RETEST",
                "scenario": "Completed book-rule buy retest is aligned.",
                "trigger": "Enter on the completed retest candle.",
                "closed_candle_key": "closed-bootstrap",
                "closed_candle_sequence": 1,
                "pair": "USD/CAD OTC",
                "timeframe": "M5",
                "rule_traceability": {
                    "selected_book_rule_ids": ["RULE_A", "RULE_B"],
                },
            },
        },
    }

    result = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="book_rules")
    assert result["side"] == "BUY"
    assert result["source"] == "direct_book_rule_signal"


































def test_coerce_box_mapping_handles_box_entries_without_unpacking_error():
    bridge = _load_bridge_module()
    payload = {
        "buy_button": {"x": 100, "y": 200},
        "sell_button": [300, 400],
    }

    result = bridge._coerce_box_mapping(payload)
    assert result["buy_button"] == (100, 200)
    assert result["sell_button"] == (300, 400)


def test_coerce_box_mapping_reads_runtime_trigger_manifest_aliases():
    bridge = _load_bridge_module()
    payload = {
        "boxes": {
            "buy_click": {"x": 1841, "y": 232},
            "sell_click": {"x": 1841, "y": 262},
            "chart_anchor": {"x": 1219, "y": 789},
        },
        "actions": {
            "buy": {"click": {"x": 1841, "y": 232}},
            "sell": {"click": {"x": 1841, "y": 262}},
        },
    }

    result = bridge._coerce_box_mapping(payload)
    assert result["buy_button"] == (1841, 232)
    assert result["sell_button"] == (1841, 262)
    assert result["chart_anchor"] == (1219, 789)


def test_bridge_trigger_state_blocks_duplicate_same_signal():
    bridge = _load_bridge_module()
    state = bridge._BridgeTriggerState(rearm_seconds=0.0, flip_guard_seconds=0.0)
    trade = {"side": "BUY", "signal_id": "sig-1", "published_epoch": 1.0, "candle_key": "c-1", "timeframe_seconds": 300}

    assert state.should_trigger(trade) == (True, "triggered")
    assert state.should_trigger(trade) == (False, "unchanged_live_state")


def test_bridge_trigger_state_blocks_duplicate_visual_cycle_even_when_signal_id_changes():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_visual_cycle")
    state = bridge._BridgeTriggerState(rearm_seconds=0.0, flip_guard_seconds=0.0, max_trades_per_candle=0)
    first = {
        "side": "BUY",
        "signal_id": "sig-1",
        "published_epoch": 1.0,
        "candle_key": "c-1",
        "timeframe_seconds": 300,
        "trigger_token": "L9 BUY TRANSITION|BUY|HOLD|BUY|FORMING|1|GREEN|156|156",
    }
    second = {
        **first,
        "signal_id": "sig-2",
        "published_epoch": 2.0,
    }

    assert state.should_trigger(first) == (True, "triggered")
    assert state.should_trigger(second) == (False, "unchanged_live_state")


def test_bridge_trigger_state_blocks_immediate_opposite_side_flip_with_guard():
    bridge = _load_bridge_module()
    state = bridge._BridgeTriggerState(
        rearm_seconds=0.0,
        flip_guard_seconds=30.0,
        lock_side_per_candle=True,
    )

    assert state.should_trigger({"side": "BUY", "signal_id": "sig-1", "published_epoch": 1.0, "candle_key": "c-1", "timeframe_seconds": 300}) == (True, "triggered")
    assert state.should_trigger({"side": "SELL", "signal_id": "sig-2", "published_epoch": 2.0, "candle_key": "c-1", "timeframe_seconds": 300}) == (False, "opposite_side_blocked_same_candle")


def test_bridge_trigger_state_defaults_to_one_trade_per_candle():
    bridge = _load_bridge_module()
    state = bridge._BridgeTriggerState(rearm_seconds=0.0, flip_guard_seconds=0.0)

    first = {"side": "BUY", "signal_id": "sig-1", "published_epoch": 1.0, "candle_key": "c-42", "timeframe_seconds": 300}
    second = {"side": "BUY", "signal_id": "sig-2", "published_epoch": 2.0, "candle_key": "c-42", "timeframe_seconds": 300}

    assert state.should_trigger(first) == (True, "triggered")
    assert state.should_trigger(second) == (False, "candle_trade_limit_reached")


def test_bridge_trigger_state_can_limit_trades_per_candle_when_requested():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_candle_limit")
    state = bridge._BridgeTriggerState(
        rearm_seconds=0.0,
        flip_guard_seconds=0.0,
        max_trades_per_candle=1,
    )

    first = {"side": "BUY", "signal_id": "sig-1", "published_epoch": 1.0, "candle_key": "c-42", "timeframe_seconds": 300}
    second = {"side": "SELL", "signal_id": "sig-2", "published_epoch": 2.0, "candle_key": "c-42", "timeframe_seconds": 300}

    assert state.should_trigger(first) == (True, "triggered")
    assert state.should_trigger(second) == (False, "candle_trade_limit_reached")


def test_resolve_trade_payload_includes_candle_identity_and_timeframe_seconds():
    bridge = _load_bridge_module()
    now = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "latest_signal": {
            "signal_id": "sig-99",
            "published_epoch": now,
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "action": "BUY",
                "actionable": True,
                "status": "STRUCTURE_CONTINUATION_READY",
                "closed_candle_key": "abc123",
                "closed_candle_sequence": 77,
                "pair": "USD/CAD OTC",
                "timeframe": "M5",
            },
            "market_study_v3": {
                "closed_candle_key": "abc123",
                "closed_candle_sequence": 77,
            },
        },
    }

    trade = bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="book_rules")
    assert trade["candle_key"] == "abc123"
    assert trade["candle_sequence"] == 77
    assert trade["timeframe_seconds"] == 300








def test_resolve_trade_payload_rejects_waiting_book_rule_signal():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_waiting_book_rule")
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "latest_signal": {
            "execution_action": "HOLD",
            "actionable": False,
            "execution_permission": "WAIT",
            "published_epoch": time.time(),
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "WAITING_FOR_CURRENT_BOOK_TRIGGER",
                "action": "WAIT",
                "watch_side": "BUY",
                "actionable": False,
                "closed_candle_key": "closed-02",
                "closed_candle_sequence": 11,
            },
            "market_study_v3": {
                "closed_candle_key": "closed-02",
                "closed_candle_sequence": 11,
            },
        },
    }

    try:
        bridge._resolve_trade_payload(payload, score_threshold=0.0, signal_source="book_rules")
    except bridge.TradeRejected as exc:
        assert "No actionable live signal" in str(exc)
    else:
        raise AssertionError("Expected TradeRejected when the book-rule signal is still WAIT")








def test_bridge_defaults_trigger_manifest_to_durable_user_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    local_app_data = tmp_path / "localappdata"
    manifest_path = local_app_data / "PhoenixGuard" / "calibration" / "trigger_calibration_manifest.json"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("{}", encoding="utf-8")

    monkeypatch.delenv("PHOENIXGUARD_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_RUNTIME_LOCK_PATH", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_CALIBRATION_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_localappdata")
    assert bridge.DEFAULT_TRIGGER_MANIFEST == manifest_path
    assert bridge._calibration_manifest_paths(bridge.PROJECT_ROOT) == [manifest_path.resolve()]


def test_read_live_state_prefers_fresher_local_runtime_snapshot(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_prefer_local_runtime")
    runtime_dir = tmp_path / "runtime" / "live"
    session_dir = runtime_dir / "data_live" / "mobile_api" / "window_tracker" / "sessions" / "pocket-live-8788"
    session_dir.mkdir(parents=True)
    local_state_path = session_dir / "compact_live_state.json"
    local_payload = {
        "session_id": "pocket-live-8788",
        "last_capture_epoch": 200.0,
        "latest_signal": {
            "published_epoch": 200.0,
            "action": "SELL",
        },
    }
    local_state_path.write_text(json.dumps(local_payload), encoding="utf-8")

    def _fake_runtime_dir(project_root: Path | None = None) -> Path:
        return runtime_dir

    def _fake_read_json_url(url: str, timeout_sec: float) -> dict[str, object]:
        return {
            "session_id": "pocket-live-8788",
            "last_capture_epoch": 100.0,
            "latest_signal": {
                "published_epoch": 100.0,
                "action": "BUY",
            },
        }

    monkeypatch.setattr(bridge, "_default_live_runtime_dir", _fake_runtime_dir)
    monkeypatch.setattr(bridge, "_read_json_url", _fake_read_json_url)

    payload = bridge._read_live_state(
        base_url="http://127.0.0.1:8793",
        session_id="pocket-live-8788",
        timeout_sec=30.0,
    )
    assert payload["_bridge_state_source"] == "local_runtime_file"
    assert payload["last_capture_epoch"] == 200.0
    assert payload["latest_signal"]["action"] == "SELL"


def test_read_fresh_trade_does_not_spin_until_timeout_on_a_stale_snapshot(monkeypatch: pytest.MonkeyPatch):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_stale_single_read")
    calls = 0
    stale_epoch = time.time() - 60
    payload = {
        "session_id": "pocket-live-8788",
        "last_capture_epoch": stale_epoch,
        "display_capture_epoch": stale_epoch,
        "latest_signal": {
            "published_epoch": stale_epoch,
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "ROLE_FLIP_RETEST_READY",
                "action": "SELL",
                "watch_side": "SELL",
                "actionable": True,
                "closed_candle_key": "closed-stale-single-read",
                "closed_candle_sequence": 21,
            },
        },
    }

    def read_state(**_kwargs: object):
        nonlocal calls
        calls += 1
        return payload

    monkeypatch.setattr(bridge, "_read_live_state", read_state)

    with pytest.raises(bridge.TradeRejected, match="Live signal stale"):
        bridge._read_fresh_trade(
            base_url="http://127.0.0.1:8793",
            session_id="pocket-live-8788",
            timeout_sec=30,
            score_threshold=0,
            max_signal_age_seconds=15,
        )

    assert calls == 1


def test_resolve_trade_payload_reports_freshness_metrics_on_success():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_freshness_metrics")
    now = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "last_capture_epoch": now - 0.4,
        "display_capture_epoch": now - 0.3,
        "display_published_epoch": now - 0.2,
        "latest_signal": {
            "published_epoch": now - 0.5,
            "signal_age_sec": 0.5,
            "freshness_window_sec": 300.0,
            "freshness_score": 1.0,
            "pipeline_latency_sec": 0.18,
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "ROLE_FLIP_RETEST_READY",
                "action": "BUY",
                "watch_side": "BUY",
                "actionable": True,
                "closed_candle_key": "closed-freshness",
                "closed_candle_sequence": 44,
            },
        },
    }

    trade = bridge._resolve_trade_payload(payload, score_threshold=0.0)
    assert trade["signal_age_seconds"] < 1.0
    assert trade["published_age_seconds"] >= 0.0
    assert trade["capture_age_seconds"] >= 0.0
    assert trade["display_capture_age_seconds"] >= 0.0
    assert trade["pipeline_latency_seconds"] == 0.18


def test_strategist_uses_fresh_observation_timestamp_instead_of_stale_signal_age_field():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_fresh_observation")
    now = time.time()
    payload = {
        "session_id": "pocket-live-8788",
        "timeframe": "M5",
        "last_capture_epoch": now - 0.2,
        "display_capture_epoch": now - 0.3,
        "display_published_epoch": now - 0.25,
        "latest_signal": {
            "published_epoch": now - 0.2,
            # A producer bug may publish a misleading age field; observation
            # epochs are the liveness truth and must win.
            "signal_age_sec": 75.0,
            "freshness_window_sec": 300.0,
            "freshness_score": 1.0,
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "ROLE_FLIP_RETEST_READY",
                "action": "SELL",
                "watch_side": "SELL",
                "actionable": True,
                "closed_candle_key": "closed-fresh-observation",
                "closed_candle_sequence": 55,
            },
        },
    }

    trade = bridge._resolve_trade_payload(payload, score_threshold=0.0, max_signal_age_seconds=15.0)
    assert trade["side"] == "SELL"
    assert trade["signal_age_seconds"] < 2.0


def test_trigger_calibration_defaults_output_to_durable_user_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    local_app_data = tmp_path / "localappdata"
    calibration_dir = local_app_data / "PhoenixGuard" / "calibration"

    monkeypatch.delenv("PHOENIXGUARD_RUNTIME_DIR", raising=False)
    monkeypatch.delenv("PHOENIXGUARD_CALIBRATION_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))

    calibration = _load_calibration_module("phoenixguard_trigger_calibration_localappdata")
    assert calibration.DEFAULT_OUTPUT == calibration_dir / "trigger_calibration_manifest.json"


def test_trigger_calibration_writes_atomic_primary_and_backup(tmp_path: Path):
    calibration = _load_calibration_module("phoenixguard_trigger_calibration_backup")
    output = tmp_path / "trigger_calibration_manifest.json"
    manifest = {"boxes": {"buy_click": {"x": 1, "y": 2}, "sell_click": {"x": 3, "y": 4}}}

    calibration._write_manifest(output, manifest)

    assert json.loads(output.read_text(encoding="utf-8")) == manifest
    backup = tmp_path / "trigger_calibration_manifest.backup.json"
    assert json.loads(backup.read_text(encoding="utf-8")) == manifest
    assert not output.with_suffix(".json.tmp").exists()


def test_trigger_calibration_manifest_stores_saved_timing_policy():
    calibration = _load_calibration_module("phoenixguard_trigger_calibration_timing")
    manifest = calibration._build_manifest(
        chart_anchor=(10, 20),
        buy_click=(30, 40),
        sell_click=(50, 60),
        fixed_expiry_seconds=180,
        fixed_amount=1.0,
        score_threshold=0.0,
        chart_focus_settle_seconds=5.0,
        pre_click_delay_seconds=6.0,
        inter_click_delay_seconds=5.0,
        pointer_move_duration_seconds=0.4,
    )

    assert manifest["timing_policy"] == {
        "chart_focus_settle_seconds": 5.0,
        "pre_click_delay_seconds": 6.0,
        "inter_click_delay_seconds": 5.0,
        "pointer_move_duration_seconds": 0.4,
    }


def test_trigger_manifest_to_boxes_uses_saved_timing_policy():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_timing")
    manifest = {
        "boxes": {
            "chart_anchor": {"x": 100, "y": 200},
            "buy_click": {"x": 300, "y": 400},
            "sell_click": {"x": 500, "y": 600},
        },
        "fixed_amount": 2.0,
        "fixed_expiry_seconds": 180,
        "timing_policy": {
            "chart_focus_settle_seconds": 5.0,
            "pre_click_delay_seconds": 8.0,
            "inter_click_delay_seconds": 0.6,
            "pointer_move_duration_seconds": 0.5,
        },
    }

    boxes, chart_anchor, fixed_amount, fixed_expiry, timing_policy = bridge._trigger_manifest_to_boxes(manifest)
    assert boxes["buy_click"] == (300, 400)
    assert chart_anchor == (100, 200)
    assert fixed_amount == 2.0
    assert fixed_expiry == 180
    assert timing_policy == {
        "chart_focus_settle_seconds": 5.0,
        "pre_click_delay_seconds": 8.0,
        "inter_click_delay_seconds": 0.6,
        "pointer_move_duration_seconds": 0.5,
    }


def test_send_direct_clicks_honors_saved_delays(monkeypatch: pytest.MonkeyPatch):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_click_timing")
    recorded_calls: list[tuple[object, ...]] = []
    recorded_sleeps: list[float] = []

    def _move_to(x: float, y: float, duration: float = 0.0) -> None:
        recorded_calls.append(("moveTo", x, y, duration))

    def _click(x: float, y: float) -> None:
        recorded_calls.append(("click", x, y))

    def _sleep(seconds: float) -> None:
        recorded_sleeps.append(seconds)

    fake_pyautogui = SimpleNamespace(moveTo=_move_to, click=_click)
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(bridge.time, "sleep", _sleep)

    bridge._send_direct_clicks(
        {"buy_click": (30, 40)},
        chart_anchor=(10, 20),
        side="BUY",
        expiry_seconds=180,
        fixed_amount=1.0,
        timing_policy={
            "chart_focus_settle_seconds": 5.0,
            "pre_click_delay_seconds": 7.0,
            "inter_click_delay_seconds": 0.5,
            "pointer_move_duration_seconds": 0.4,
        },
    )

    assert recorded_calls == [
        ("moveTo", 10, 20, 0.4),
        ("click", 10, 20),
        ("moveTo", 30, 40, 0.4),
        ("click", 30, 40),
        ("click", 30, 40),
    ]
    assert recorded_sleeps == [7.0, 0.5]


def test_send_direct_clicks_aborts_when_refreshed_side_drifts(monkeypatch: pytest.MonkeyPatch):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_click_drift_abort")
    events: list[tuple[object, ...]] = []

    def _move_to(x: float, y: float, duration: float = 0.0) -> None:
        events.append(("moveTo", x, y, duration))

    def _click(x: float, y: float) -> None:
        events.append(("click", x, y))

    def _sleep(seconds: float) -> None:
        events.append(("sleep", seconds))

    fake_pyautogui = SimpleNamespace(moveTo=_move_to, click=_click)
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(bridge.time, "sleep", _sleep)

    def refresh_trade():
        events.append(("refresh",))
        return {"side": "SELL"}

    with pytest.raises(bridge.TradeRejected, match="decision was BUY"):
        bridge._send_direct_clicks(
            {"buy_click": (30, 40), "sell_click": (50, 60)},
            chart_anchor=(10, 20),
            side="BUY",
            expiry_seconds=180,
            timing_policy={
                "chart_focus_settle_seconds": 0.0,
                "pre_click_delay_seconds": 5.0,
                "inter_click_delay_seconds": 0.4,
                "pointer_move_duration_seconds": 0.4,
            },
            refresh_trade_before_click=refresh_trade,
        )

    # No clicks may land after a failed confirmation.
    assert ("click", 50, 60) not in events


def test_send_direct_clicks_fires_when_refresh_confirms_decision(monkeypatch: pytest.MonkeyPatch):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_click_confirm")
    events: list[tuple[object, ...]] = []

    def _move_to(x: float, y: float, duration: float = 0.0) -> None:
        events.append(("moveTo", x, y, duration))

    def _click(x: float, y: float) -> None:
        events.append(("click", x, y))

    def _sleep(seconds: float) -> None:
        events.append(("sleep", seconds))

    fake_pyautogui = SimpleNamespace(moveTo=_move_to, click=_click)
    monkeypatch.setitem(sys.modules, "pyautogui", fake_pyautogui)
    monkeypatch.setattr(bridge.time, "sleep", _sleep)

    def refresh_trade():
        events.append(("refresh",))
        return {"side": "BUY"}

    result = bridge._send_direct_clicks(
        {"buy_click": (30, 40), "sell_click": (50, 60)},
        chart_anchor=(10, 20),
        side="BUY",
        expiry_seconds=180,
        expected_candle_key="candle-live",
        timing_policy={
            "chart_focus_settle_seconds": 0.0,
            "pre_click_delay_seconds": 5.0,
            "inter_click_delay_seconds": 0.4,
            "pointer_move_duration_seconds": 0.4,
        },
        refresh_trade_before_click=refresh_trade,
    )

    assert events == [
        ("moveTo", 10, 20, 0.4),
        ("click", 10, 20),
        ("sleep", 5.0),
        ("refresh",),
        ("moveTo", 30, 40, 0.4),
        ("click", 30, 40),
        ("sleep", 0.4),
        ("click", 30, 40),
    ]
    assert result == {"executed_side": "BUY", "refreshed_before_click": True, "press_count": 2}


def test_bridge_trigger_state_starts_cooldown_after_ten_executed_trades(monkeypatch: pytest.MonkeyPatch):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_cooldown")
    clock = {"now": 1000.0}
    monkeypatch.setattr(bridge.time, "time", lambda: clock["now"])

    state = bridge._BridgeTriggerState(
        rearm_seconds=0.0,
        flip_guard_seconds=0.0,
        max_trades_per_candle=1,
        max_trades_per_session=0,
        cooldown_after_trades=10,
        cooldown_seconds=480.0,
    )

    for index in range(10):
        trade = {
            "side": "BUY",
            "signal_id": f"sig-{index}",
            "published_epoch": float(index + 1),
            "candle_key": f"c-{index}",
            "timeframe_seconds": 300,
        }
        assert state.should_trigger(trade) == (True, "triggered")
        state.record_trade_execution()

    blocked_trade = {
        "side": "BUY",
        "signal_id": "sig-blocked",
        "published_epoch": 99.0,
        "candle_key": "c-blocked",
        "timeframe_seconds": 300,
    }
    assert state.should_trigger(blocked_trade) == (False, "cooldown_active")

    clock["now"] += 481.0
    assert state.should_trigger(blocked_trade) == (True, "triggered")


def test_bridge_trigger_state_default_cooldown_is_disabled():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_no_default_cooldown")
    state = bridge._BridgeTriggerState(max_trades_per_session=0)

    for index in range(12):
        trade = {
            "side": "BUY" if index % 2 == 0 else "SELL",
            "signal_id": f"sig-{index}",
            "published_epoch": float(index + 1),
            "candle_key": f"c-{index}",
            "timeframe_seconds": 300,
        }
        assert state.should_trigger(trade) == (True, "triggered")
        state.record_trade_execution()


def test_bridge_trigger_state_stops_after_eight_executed_contracts():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_session_trade_cap")
    state = bridge._BridgeTriggerState(max_trades_per_candle=1)

    for index in range(4):
        trade = {
            "side": "BUY",
            "signal_id": f"sig-{index}",
            "published_epoch": float(index + 1),
            "candle_key": f"c-{index}",
        }
        assert state.should_trigger(trade) == (True, "triggered")
        state.record_trade_execution(trade)

    assert state.should_trigger(
        {"side": "BUY", "signal_id": "sig-4", "published_epoch": 5.0, "candle_key": "c-4"}
    ) == (False, "session_trade_limit_reached")










def test_read_live_state_attaches_direct_visual_bias_sidecar(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    bridge = _load_bridge_module("phoenixguard_direct_visual_bias_sidecar")
    runtime_dir = tmp_path / "runtime" / "live"
    session_dir = (
        runtime_dir
        / "data_live"
        / "mobile_api"
        / "window_tracker"
        / "sessions"
        / "pocket-live-8788"
    )
    session_dir.mkdir(parents=True)
    (session_dir / "compact_live_state.json").write_text(
        json.dumps({"session_id": "pocket-live-8788", "last_capture_epoch": 100.0}),
        encoding="utf-8",
    )
    (session_dir / "direct_visual_bias_v3.json").write_text(
        json.dumps(
            {
                "schema_version": "PG_DIRECT_VISUAL_BIAS_V3",
                "side": "SELL",
                "observed_epoch": 101.0,
            }
        ),
        encoding="utf-8",
    )
    def _fake_runtime_dir(project_root: Path | None = None) -> Path:
        return runtime_dir

    monkeypatch.setattr(
        bridge, "_default_live_runtime_dir", _fake_runtime_dir
    )

    payload = bridge._read_live_state(
        base_url="http://127.0.0.1:8793",
        session_id="pocket-live-8788",
        timeout_sec=1.0,
    )

    assert payload["direct_visual_bias_v3"]["side"] == "SELL"


def test_bridge_listener_uses_phoenixguard_session_updates(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_listener")
    runtime_dir = tmp_path / "runtime" / "live"
    runtime_dir.mkdir(parents=True)
    def _fake_runtime_dir(project_root: Path | None = None) -> Path:
        return runtime_dir

    monkeypatch.setattr(
        bridge, "_default_live_runtime_dir", _fake_runtime_dir
    )
    now_epoch = time.time()
    event = {
        "session_id": "pocket-live-8788",
        "latest_signal": {
            "published_epoch": now_epoch,
            "book_rule_action_signal_v3": {
                "schema_version": "PG_BOOK_RULE_ACTION_SIGNAL_V3",
                "status": "ROLE_FLIP_RETEST_READY",
                "action": "BUY",
                "watch_side": "BUY",
                "actionable": True,
                "confidence": 0.9,
                "playbook": "ROLE_FLIP_RETEST",
                "closed_candle_key": "closed-listener",
                "closed_candle_sequence": 7,
                "pair": "USD/JPY OTC",
                "timeframe": "M5",
            },
        },
    }

    class _StreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def __iter__(self):
            return iter(
                [
                    b": heartbeat\n",
                    b"\n",
                    b"event: SESSION_UPDATE\n",
                    f"data: {json.dumps(event)}\n".encode("utf-8"),
                    b"\n",
                ]
            )

    def _fake_urlopen(*_args: object, **_kwargs: object) -> _StreamResponse:
        return _StreamResponse()

    monkeypatch.setattr(bridge.request, "urlopen", _fake_urlopen)

    update = next(
        bridge._iter_phoenixguard_session_updates(
            base_url="http://127.0.0.1:8793",
            session_id="pocket-live-8788",
            timeout_sec=30.0,
        )
    )
    trade = bridge._trade_from_listener_payload(
        update,
        base_url="http://127.0.0.1:8793",
        session_id="pocket-live-8788",
        score_threshold=0.0,
        fixed_expiry_seconds_override=180,
        max_signal_age_seconds=15.0,
    )

    assert update == event
    assert trade["side"] == "BUY"
    assert trade["source"] == "direct_book_rule_signal"
    assert trade["state_source"] == "phoenixguard_session_stream"
    assert trade["expiry_seconds"] == 180


def test_bridge_uses_listener_transport_by_default():
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_listener_default")

    parsed = bridge._build_parser().parse_args([])
    assert parsed.transport == "listener"
    assert parsed.signal_source == "strategist"


def test_bridge_once_mode_uses_the_phoenixguard_listener(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    bridge = _load_bridge_module("phoenixguard_direct_trade_bridge_listener_once")

    class NoopLock:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def acquire(self) -> None:
            return None

        def release(self) -> None:
            return None

    event = {"session_id": "pocket-live-8788", "direct_visual_bias_v3": {"side": "SELL"}}

    def _fake_session_updates(**_kwargs: object):
        return iter([event])

    def _fake_trade_from_listener(payload: dict[str, Any], **_kwargs: object):
        return {
            "side": payload["direct_visual_bias_v3"]["side"],
            "state_source": "phoenixguard_session_stream",
        }

    def _fail_trade_once(**_kwargs: object):
        raise AssertionError("poll fallback ran")

    monkeypatch.setattr(bridge, "_InstanceLock", NoopLock)
    monkeypatch.setattr(bridge, "_iter_phoenixguard_session_updates", _fake_session_updates)
    monkeypatch.setattr(bridge, "_trade_from_listener_payload", _fake_trade_from_listener)
    monkeypatch.setattr(bridge, "_trade_once", _fail_trade_once)

    assert bridge.main(["--once", "--dry-run", "--transport", "listener"]) == 0
    assert '"state_source": "phoenixguard_session_stream"' in capsys.readouterr().out
