from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping, cast


def _load_bridge_module():
    module_path = Path(__file__).resolve().parents[2] / "Backend" / "tools" / "phoenixguard_mt4_file_bridge.py"
    spec = importlib.util.spec_from_file_location("phoenixguard_mt4_file_bridge", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample_execution_packet() -> dict[str, object]:
    professional_plan: dict[str, object] = {
        "schema_version": "PG_PROFESSIONAL_TRADE_PLAN_V3",
        "side": "BUY",
        "authority_side": "BUY",
        "professional_grade": True,
        "blocker": "",
        "next_required": "none",
        "thesis_class": "TREND_ALIGNED_CONTINUATION",
        "professional_thesis_state": "PRIMARY_BIAS_ALIGNED",
        "entry_window": {"duration_sec": 300, "candle_count": 1},
        "thesis_horizon": {
            "expected_duration_sec": 3600,
            "expected_candle_count": 12,
            "minimum_professional_candles": 8,
            "current_leg_candle_count": 6,
            "current_leg_side": "BUY",
            "current_leg_stage": "MATURE",
            "estimated_candles_to_force": 18,
        },
    }
    return {
        "schema_version": "PG_EXECUTION_PACKET_V3",
        "packet_id": "pgpkt_test_001",
        "session_id": "pocket-live-8788",
        "symbol": "EURJPY-OTC",
        "timeframe": "M5",
        "frame_id": 12,
        "capture_count": 34,
        "state_version": 56,
        "created_epoch_sec": 1782000000.0,
        "valid_until_epoch_sec": 1782000002.5,
        "execution": {
            "enabled": True,
            "state": "EXECUTABLE",
            "side": "BUY",
            "expiry_seconds": 600,
            "amount_action": "DO_NOT_CHANGE_AMOUNT",
            "time_sequence": {"target_seconds": 600, "target_text": "00:10:00"},
            "reason_codes": ["CLEAN_WAVE"],
        },
        "model_council": {
            "final_state": "EXECUTABLE",
            "final_side": "BUY",
            "dominance_margin": 0.73,
            "sequence_context": {
                "sequence_status": "COMPLETE",
                "sequence_length": 3,
                "sequence_confidence": 0.81,
            },
        },
        "live_integrity": {
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": "frame_hash_test",
        },
        "runtime_model_health": {"all_required_models_awake": True},
        "trade_permission": {"executable_allowed": True},
        "allowance_package": {
            "schema_version": "PG_ALLOWANCE_PACKAGE_V1",
            "package_type": "INTRADAY_ENTER_NOW",
            "allowance_family": "INTRADAY",
            "execution_authority": "PLAYBOOK_FINAL_DECIDER_V3",
            "packet_authority": "PG_EXECUTION_PACKET_V3",
            "side": "BUY",
            "accepted": True,
            "decision_accepted": True,
            "execution_ready": True,
            "entry_now_allowed": True,
            "timing_mode": "ENTER_NOW",
            "selected_lane": "SNIPER_ZONE_ENTRY",
            "score": 0.83,
            "threshold": 0.70,
            "entry_window": {"duration_sec": 300, "candle_count": 1},
            "thesis_horizon": professional_plan["thesis_horizon"],
            "expected_move_time": {
                "expected_duration_sec": 3600,
                "expected_candle_count": 12,
            },
            "professional_trade_plan": professional_plan,
            "professional_thesis_state": "PRIMARY_BIAS_ALIGNED",
            "professional_authority_side": "BUY",
        },
    }


def _sample_playbook_ai_intelligence() -> dict[str, object]:
    return {
        "schema_version": "PG_PLAYBOOK_AI_INTELLIGENCE_V3",
        "semantic_graph": {
            "interpretation": "FULL_OVERLAY_SUITE_THESIS",
            "coverage": {
                "rows_total": 42,
                "actionable_count": 30,
                "same_side_actionable_count": 24,
                "entry_window_count": 4,
                "same_side_entry_window_count": 4,
                "target_window_count": 3,
                "opposing_force_count": 2,
                "invalidation_count": 2,
                "prediction_path_count": 2,
                "structure_box_count": 4,
                "trendline_count": 2,
                "overlay_arsenal_score": 0.91,
                "expected_move_candles": 12,
                "full_suite_ready": True,
            },
        },
        "regime_router": {
            "regime": "STRUCTURE_CONFIRMED_TREND_CONTINUATION",
            "route": "TREND_CONTINUATION_THESIS",
            "route_side": "BUY",
            "confidence": 0.84,
            "current_leg_side": "BUY",
            "current_leg_stage": "MATURE",
        },
        "thesis_arbitration": {
            "candidate_side": "BUY",
            "winner": "BUY",
            "winning_score": 0.86,
            "margin": 0.31,
            "candidate_score": 0.86,
            "candidate_supported": True,
            "conflict": False,
            "state": "CANDIDATE_THESIS_LEADS",
            "scores": {
                "BUY": {"side": "BUY", "score": 0.86, "components": {}},
                "SELL": {"side": "SELL", "score": 0.55, "components": {}},
            },
        },
        "meta_label": {
            "selected_side": "BUY",
            "candidate_tradeable": True,
            "selected": {
                "target_before_invalidation_probability": 0.72,
                "invalidation_first_risk": 0.28,
                "label": "TARGET_BEFORE_INVALIDATION_LIKELY",
            },
        },
        "horizon": {
            "selected_side": "BUY",
            "selected": {
                "optimized_candle_count": 12,
                "optimized_duration_sec": 3600,
                "optimized_duration_text": "1h",
                "horizon_class": "STRUCTURE_LEG_6_12_CANDLES",
                "basis": "professional_trade_plan_thesis_horizon",
                "target_before_invalidation_probability": 0.72,
            },
        },
        "rules_applied": ["buy_and_sell_theses_scored_simultaneously"],
    }


def test_mt4_bridge_compact_command_preserves_ea_contract() -> None:
    bridge = _load_bridge_module()
    command = bridge._compact_command(_sample_execution_packet(), bridge_sequence=7)

    bridge._validate_command(command)
    encoded = bridge._json_dumps(command)
    decoded = json.loads(encoded)

    assert decoded["schema_version"] == "PG_MT4_EXECUTION_COMMAND_V1"
    assert decoded["packet_id"] == "pgpkt_test_001"
    assert decoded["symbol"] == "EURJPY-OTC"
    assert decoded["bridge_sequence"] == 7
    assert decoded["created_epoch_sec"] > decoded["source_created_epoch_sec"]
    assert decoded["source_created_epoch_sec"] == 1782000000.0
    assert decoded["heartbeat"]["source_created_epoch_sec"] == 1782000000.0
    assert decoded["execution"]["state"] == "EXECUTABLE"
    assert decoded["execution"]["side"] == "BUY"
    assert decoded["entry_eligibility"]["eligible"] is True
    assert decoded["entry_eligibility"]["allowance_package_type"] == "INTRADAY_ENTER_NOW"
    assert decoded["allowance_package"]["schema_version"] == "PG_ALLOWANCE_PACKAGE_V1"
    assert decoded["allowance_package"]["package_type"] == "INTRADAY_ENTER_NOW"
    assert decoded["allowance_package"]["source_present"] is True
    assert decoded["allowance_package"]["inferred"] is False
    assert decoded["allowance_package"]["selected_lane"] == "SNIPER_ZONE_ENTRY"
    assert decoded["allowance_package"]["professional_grade"] is True
    assert decoded["allowance_package"]["professional_trade_plan"]["expected_candle_count"] == 12
    assert decoded["allowance_package"]["expected_move_time"]["expected_duration_sec"] == 3600
    assert decoded["expected_move_time"] == decoded["allowance_package"]["expected_move_time"]
    assert decoded["professional_trade_plan"] == decoded["allowance_package"]["professional_trade_plan"]
    assert decoded["execution"]["allowance_package_type"] == "INTRADAY_ENTER_NOW"
    assert decoded["permission_state"]["entry_eligible"] is True
    assert decoded["reason_codes"] == ["CLEAN_WAVE"]
    assert decoded["heartbeat"]["alive"] is True
    assert decoded["execution"]["amount_action"] == "DO_NOT_CHANGE_AMOUNT"
    assert decoded["live_integrity"]["source"] == "model_council"
    assert decoded["live_integrity"]["input_frame_hash"] == "frame_hash_test"


def test_mt4_bridge_compacts_playbook_ai_summary_without_full_nested_payload() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    ai_intelligence = _sample_playbook_ai_intelligence()
    packet["playbook_ai_intelligence_v3"] = ai_intelligence
    allowance = cast(dict[str, object], packet["allowance_package"])
    allowance["playbook_ai_intelligence_v3"] = ai_intelligence

    command = bridge._compact_command(packet, bridge_sequence=9)

    bridge._validate_command(command)
    summary = cast(dict[str, object], command["playbook_ai_summary_v3"])
    arbitration = cast(dict[str, object], summary["thesis_arbitration"])
    meta = cast(dict[str, object], summary["meta_label"])
    horizon = cast(dict[str, object], summary["horizon"])
    assert summary["schema_version"] == "PG_PLAYBOOK_AI_SUMMARY_V3"
    assert arbitration["winner"] == "BUY"
    assert meta["target_before_invalidation_probability"] == 0.72
    assert horizon["optimized_candle_count"] == 12
    assert "semantic_graph" not in summary
    assert "playbook_ai_intelligence_v3" not in command
    command_allowance = cast(dict[str, object], command["allowance_package"])
    assert command_allowance["playbook_ai_summary_v3"] == summary
    assert len(bridge._json_dumps(command).encode("utf-8")) < bridge.SLOT_BYTES


def test_mt4_bridge_compact_command_accepts_mt4_symbol_and_timeframe_override() -> None:
    bridge = _load_bridge_module()
    command = bridge._compact_command(
        _sample_execution_packet(),
        bridge_sequence=8,
        symbol_override="EURCADm",
        timeframe_override="M5",
    )

    bridge._validate_command(command)

    assert command["symbol"] == "EURCADm"
    assert command["timeframe"] == "M5"
    assert command["packet_id"] == "pgpkt_test_001"


def test_mt4_bridge_compact_command_preserves_expected_move_time_from_professional_plan() -> None:
    bridge = _load_bridge_module()
    packet = json.loads(json.dumps(_sample_execution_packet()))
    allowance = cast(dict[str, object], packet["allowance_package"])
    allowance.pop("expected_move_time")
    professional = cast(dict[str, object], allowance["professional_trade_plan"])
    professional["expected_move_time"] = {
        "expected_duration_text": "40m",
        "expected_duration_sec": 2400,
        "expected_candle_count": 8,
    }

    command = bridge._compact_command(packet, bridge_sequence=12)

    bridge._validate_command(command)
    assert command["allowance_package"]["expected_move_time"]["expected_duration_text"] == "40m"
    assert command["allowance_package"]["expected_move_time"]["expected_duration_sec"] == 2400
    assert command["expected_move_time"] == command["allowance_package"]["expected_move_time"]


def test_mt4_bridge_compact_command_preserves_swing_allowance_package() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    packet["allowance_package"] = {
        "schema_version": "PG_ALLOWANCE_PACKAGE_V1",
        "package_type": "SWING",
        "allowance_family": "SWING",
            "execution_authority": "PLAYBOOK_FINAL_DECIDER_V3",
            "packet_authority": "PG_EXECUTION_PACKET_V3",
        "side": "BUY",
        "accepted": True,
        "decision_accepted": True,
        "execution_ready": True,
        "entry_now_allowed": False,
        "timing_mode": "WAIT_FOR_PULLBACK",
        "selected_lane": "SNIPER_ZONE_ENTRY",
        "score": 0.79,
        "threshold": 0.70,
        "entry_window": {"duration_sec": 300, "candle_count": 1},
        "thesis_horizon": {
            "expected_duration_sec": 3600,
            "expected_candle_count": 12,
            "minimum_professional_candles": 8,
            "current_leg_candle_count": 6,
            "current_leg_side": "BUY",
            "current_leg_stage": "MATURE",
            "estimated_candles_to_force": 18,
        },
        "professional_trade_plan": {
            "schema_version": "PG_PROFESSIONAL_TRADE_PLAN_V3",
            "side": "BUY",
            "authority_side": "BUY",
            "professional_grade": True,
            "blocker": "",
            "next_required": "none",
            "thesis_class": "TREND_ALIGNED_CONTINUATION",
            "professional_thesis_state": "PRIMARY_BIAS_ALIGNED",
            "entry_window": {"duration_sec": 300, "candle_count": 1},
            "thesis_horizon": {
                "expected_duration_sec": 3600,
                "expected_candle_count": 12,
                "minimum_professional_candles": 8,
                "current_leg_candle_count": 6,
                "current_leg_side": "BUY",
                "current_leg_stage": "MATURE",
                "estimated_candles_to_force": 18,
            },
        },
    }

    command = bridge._compact_command(packet, bridge_sequence=9)

    bridge._validate_command(command)
    assert command["allowance_package"]["package_type"] == "SWING"
    assert command["allowance_package"]["allowance_family"] == "SWING"
    assert command["entry_eligibility"]["allowance_package_type"] == "SWING"
    assert command["execution"]["allowance_package_type"] == "SWING"


def test_mt4_bridge_rejects_inferred_allowance_package() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    packet.pop("allowance_package")

    command = bridge._compact_command(packet, bridge_sequence=10)

    assert command["allowance_package"]["source_present"] is False
    assert command["allowance_package"]["inferred"] is True
    try:
        bridge._validate_command(command)
    except ValueError as exc:
        assert "explicit from Playbook final decider" in str(exc)
    else:
        raise AssertionError("bridge accepted an inferred allowance package")


def test_mt4_bridge_rejects_non_ready_allowance_package() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    allowance = dict(cast(Mapping[str, object], packet["allowance_package"]))
    allowance["execution_ready"] = False
    packet["allowance_package"] = allowance

    command = bridge._compact_command(packet, bridge_sequence=11)

    try:
        bridge._validate_command(command)
    except ValueError as exc:
        assert "execution_ready" in str(exc)
    else:
        raise AssertionError("bridge accepted a non-ready allowance package")


def test_mt4_bridge_live_monitor_rejects_stale_packet_frame() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    live = {
        "tracking_enabled": True,
        "status": "running",
        "frame_id": 18,
        "display_frame_id": 18,
        "capture_count": 18,
        "display_published_epoch": 1000.0,
        "latest_execution_packet": {"packet_id": packet["packet_id"], "side": "BUY"},
    }

    ok, reason = bridge._packet_current_in_live_monitor(
        packet,
        live,
        now_epoch=1002.0,
        max_live_age_sec=120.0,
        max_packet_frame_lag=2,
    )

    assert ok is False
    assert "packet frame lag" in reason


def test_mt4_bridge_live_monitor_rejects_missing_current_packet_identity() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    live = {
        "tracking_enabled": True,
        "status": "running",
        "frame_id": packet["frame_id"],
        "display_frame_id": packet["frame_id"],
        "capture_count": packet["capture_count"],
        "display_published_epoch": 1000.0,
        "latest_execution_packet": {"side": "BUY"},
    }

    ok, reason = bridge._packet_current_in_live_monitor(
        packet,
        live,
        now_epoch=1002.0,
        max_live_age_sec=120.0,
        max_packet_frame_lag=2,
    )

    assert ok is False
    assert "packet id missing" in reason


def test_mt4_bridge_live_monitor_rejects_expired_packet_window() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    packet["created_epoch_sec"] = 900.0
    packet["valid_until_epoch_sec"] = 1001.0
    live = {
        "tracking_enabled": True,
        "status": "running",
        "frame_id": packet["frame_id"],
        "display_frame_id": packet["frame_id"],
        "capture_count": packet["capture_count"],
        "display_published_epoch": 1000.0,
        "latest_execution_packet": {"packet_id": packet["packet_id"], "side": "BUY"},
    }

    ok, reason = bridge._packet_current_in_live_monitor(
        packet,
        live,
        now_epoch=1002.0,
        max_live_age_sec=120.0,
        max_packet_frame_lag=2,
    )

    assert ok is False
    assert "expired" in reason


def test_mt4_bridge_live_monitor_accepts_current_packet() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    live = {
        "tracking_enabled": True,
        "status": "running",
        "frame_id": packet["frame_id"],
        "display_frame_id": packet["frame_id"],
        "capture_count": packet["capture_count"],
        "display_published_epoch": 1000.0,
        "latest_execution_packet": {"packet_id": packet["packet_id"], "side": "BUY"},
    }

    ok, reason = bridge._packet_current_in_live_monitor(
        packet,
        live,
        now_epoch=1002.0,
        max_live_age_sec=120.0,
        max_packet_frame_lag=2,
    )

    assert ok is True
    assert "passed" in reason


def test_mt4_bridge_live_monitor_prefers_fresh_capture_over_stale_display_epoch() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    packet["valid_until_epoch_sec"] = 1782001000.0
    live = {
        "tracking_enabled": True,
        "status": "running",
        "frame_id": packet["frame_id"],
        "display_frame_id": packet["frame_id"],
        "capture_count": packet["capture_count"],
        "display_published_epoch": 1781999000.0,
        "last_capture_epoch": 1782000000.0,
        "latest_execution_packet": {"packet_id": packet["packet_id"], "side": "BUY"},
    }

    ok, reason = bridge._packet_current_in_live_monitor(
        packet,
        live,
        now_epoch=1782000002.0,
        max_live_age_sec=120.0,
        max_packet_frame_lag=2,
    )

    assert ok is True
    assert "live.last_capture_epoch" in reason


def test_mt4_bridge_live_monitor_accepts_fresh_performance_when_compact_live_display_lags() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    packet["valid_until_epoch_sec"] = 1782001000.0
    live = {
        "tracking_enabled": True,
        "status": "running",
        "frame_id": packet["frame_id"],
        "display_frame_id": packet["frame_id"],
        "capture_count": packet["capture_count"],
        "display_published_epoch": 1781999000.0,
        "latest_execution_packet": {"packet_id": packet["packet_id"], "side": "BUY"},
    }
    performance = {
        "frame_id": packet["frame_id"],
        "capture_count": packet["capture_count"],
        "frame_age_ms": 1500.0,
    }

    ok, reason = bridge._packet_current_in_live_monitor(
        packet,
        live,
        performance=performance,
        now_epoch=1782000002.0,
        max_live_age_sec=120.0,
        max_packet_frame_lag=2,
    )

    assert ok is True
    assert "performance.frame_age_ms" in reason


def test_mt4_bridge_live_monitor_rejects_stale_display_without_fresh_witness() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    packet["valid_until_epoch_sec"] = 1782001000.0
    live = {
        "tracking_enabled": True,
        "status": "running",
        "frame_id": packet["frame_id"],
        "display_frame_id": packet["frame_id"],
        "capture_count": packet["capture_count"],
        "display_published_epoch": 1781999000.0,
        "latest_execution_packet": {"packet_id": packet["packet_id"], "side": "BUY"},
    }

    ok, reason = bridge._packet_current_in_live_monitor(
        packet,
        live,
        now_epoch=1782000002.0,
        max_live_age_sec=120.0,
        max_packet_frame_lag=2,
    )

    assert ok is False
    assert "capture age" in reason


def test_mt4_bridge_rejects_compacted_command_that_would_fail_ea_contract() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    execution = dict(cast(Mapping[str, object], packet["execution"]))
    execution["amount_action"] = "LOCKED"
    packet["execution"] = execution

    command = bridge._compact_command(packet, bridge_sequence=8)

    try:
        bridge._validate_command(command)
    except ValueError as exc:
        assert "amount_action" in str(exc)
    else:
        raise AssertionError("bridge accepted an EA-incompatible amount_action")


def test_mt4_bridge_json_sanitizes_invalid_numbers() -> None:
    bridge = _load_bridge_module()
    payload: dict[str, Any] = {"ok": 1.0, "bad": float("nan"), "nested": {"inf": float("inf")}}

    decoded = json.loads(bridge._json_dumps(payload))

    assert decoded == {"ok": 1.0, "bad": None, "nested": {"inf": None}}


def test_mt4_bridge_atomic_write_replaces_longer_previous_payload(tmp_path: Path) -> None:
    bridge = _load_bridge_module()
    signal_path = tmp_path / "PhoenixGuard" / "mt4_execution_command.json"
    long_body = bridge._json_dumps({"schema_version": "X", "payload": "x" * 1000})
    short_body = bridge._json_dumps(bridge._status("NO_EXECUTION_PACKET", detail="none", bridge_sequence=8))

    first_latency = bridge._write_text_atomic(signal_path, long_body)
    second_latency = bridge._write_text_atomic(signal_path, short_body)
    decoded = json.loads(signal_path.read_text(encoding="utf-8"))

    assert first_latency >= 0.0
    assert second_latency >= 0.0
    assert decoded["schema_version"] == "PG_MT4_BRIDGE_STATUS_V1"
    assert decoded["bridge_status"] == "NO_EXECUTION_PACKET"
    assert decoded["bridge_sequence"] == 8


def test_mt4_alert_watcher_reads_v1_command_contract() -> None:
    script_path = Path(__file__).resolve().parents[2] / "Backend" / "tools" / "watch_trade_package_ack_alerts.ps1"
    script = script_path.read_text(encoding="utf-8")

    for expected in (
        'PG_MT4_EXECUTION_COMMAND_V1',
        'Get-NestedProperty $command "execution"',
        'Get-Mt4CommandValidUntil',
        'expected_move_time',
        'professional_trade_plan',
        'EXPECTED MOVE TIME:',
        'PROFESSIONAL PLAN:',
    ):
        assert expected in script


def test_mt4_executioner_default_packet_age_matches_live_pipeline() -> None:
    source_path = Path(__file__).resolve().parents[2] / "Backend" / "launch" / "mt4" / "PhoenixGuard_MT4_Executioner.mq4"
    source = source_path.read_text(encoding="utf-8")

    assert "input int                   InpPacketMaxAgeMs                      = 180000;" in source


def test_mt4_executioner_accepts_current_allowance_types_and_professional_holds() -> None:
    source_path = Path(__file__).resolve().parents[2] / "Backend" / "launch" / "mt4" / "PhoenixGuard_MT4_Executioner.mq4"
    source = source_path.read_text(encoding="utf-8")

    assert 'normalized == "SWING_ENTER_NOW"' in source
    assert "input int                   InpIntradayMaxHoldMinutes              = 0;" in source
    assert "if(packet.packet_id == g_lastAcceptedPacketId)" in source
    assert "packet.packet_id == g_lastAcceptedPacketId || packet.packet_id == g_lastSeenPacketId" not in source
