from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_bridge_module():
    module_path = Path(__file__).resolve().parents[1] / "tools" / "phoenixguard_mt4_file_bridge.py"
    spec = importlib.util.spec_from_file_location("phoenixguard_mt4_file_bridge", module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sample_execution_packet() -> dict[str, object]:
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
            "execution_authority": "PG_EXECUTION_PACKET_V3",
            "side": "BUY",
            "accepted": True,
            "decision_accepted": True,
            "execution_ready": True,
            "entry_now_allowed": True,
            "timing_mode": "ENTER_NOW",
            "selected_lane": "SNIPER_ZONE_ENTRY",
            "score": 0.83,
            "threshold": 0.70,
        },
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
    assert decoded["execution"]["state"] == "EXECUTABLE"
    assert decoded["execution"]["side"] == "BUY"
    assert decoded["entry_eligibility"]["eligible"] is True
    assert decoded["entry_eligibility"]["allowance_package_type"] == "INTRADAY_ENTER_NOW"
    assert decoded["allowance_package"]["schema_version"] == "PG_ALLOWANCE_PACKAGE_V1"
    assert decoded["allowance_package"]["package_type"] == "INTRADAY_ENTER_NOW"
    assert decoded["allowance_package"]["selected_lane"] == "SNIPER_ZONE_ENTRY"
    assert decoded["execution"]["allowance_package_type"] == "INTRADAY_ENTER_NOW"
    assert decoded["permission_state"]["entry_eligible"] is True
    assert decoded["reason_codes"] == ["CLEAN_WAVE"]
    assert decoded["heartbeat"]["alive"] is True
    assert decoded["execution"]["amount_action"] == "DO_NOT_CHANGE_AMOUNT"
    assert decoded["live_integrity"]["source"] == "model_council"
    assert decoded["live_integrity"]["input_frame_hash"] == "frame_hash_test"


def test_mt4_bridge_rejects_compacted_command_that_would_fail_ea_contract() -> None:
    bridge = _load_bridge_module()
    packet = _sample_execution_packet()
    execution = dict(packet["execution"])  # type: ignore[index]
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
    payload = {"ok": 1.0, "bad": float("nan"), "nested": {"inf": float("inf")}}

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
