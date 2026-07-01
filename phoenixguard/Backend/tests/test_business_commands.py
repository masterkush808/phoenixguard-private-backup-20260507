from __future__ import annotations

from copy import deepcopy
import json
from typing import Any, cast

from phoenixguard.business.command_bridge import (
    EXECUTION_COMMAND_TYPE,
    STATUS_ACCOUNT_NOT_BOUND,
    STATUS_COMMAND_TYPE,
    STATUS_DEVICE_REVOKED,
    STATUS_LICENSE_EXPIRED,
    STATUS_NO_EXECUTION_PACKET,
    STATUS_SERVICE_UNAVAILABLE,
    STATUS_UPDATE_REQUIRED,
    CommandReplayLedger,
    ConnectorAccountState,
    LocalEd25519Signer,
    build_connector_command,
    build_status_command,
    connector_poll_response,
    status_command_has_execution_authority,
    validate_connector_command,
)
from phoenixguard.execution.packet_v3 import build_execution_packet_v3
from tests.support.v3_packet_samples import complete_sequence_context_v3


NOW = 1_800_000_000.0


def _allowance_package() -> dict[str, object]:
    return {
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
        "score": 0.84,
        "threshold": 0.70,
    }


def _account(**updates: Any) -> ConnectorAccountState:
    base: dict[str, Any] = {
        "license_id": "license-a",
        "device_id": "device-a",
        "account_id": "account-a",
        "license_valid": True,
        "license_expires_at_epoch_sec": NOW + 3600.0,
        "device_revoked": False,
        "account_bound": True,
        "update_required": False,
        "service_available": True,
    }
    base.update(updates)
    return ConnectorAccountState(
        license_id=cast(str, base["license_id"]),
        device_id=cast(str, base["device_id"]),
        account_id=cast(str, base["account_id"]),
        license_valid=cast(bool, base["license_valid"]),
        license_expires_at_epoch_sec=cast(float | None, base["license_expires_at_epoch_sec"]),
        device_revoked=cast(bool, base["device_revoked"]),
        account_bound=cast(bool, base["account_bound"]),
        update_required=cast(bool, base["update_required"]),
        service_available=cast(bool, base["service_available"]),
    )


def _packet(**updates: Any) -> dict[str, Any]:
    packet = build_execution_packet_v3(
        packet_id="pgpkt-business-001",
        session_id="pocket-live-8788",
        symbol="EUR/GBP OTC",
        timeframe="M5",
        frame_id=100,
        capture_count=101,
        state_version=102,
        created_epoch=NOW - 0.2,
        valid_until_epoch=NOW + 2.0,
        side="BUY",
        expiry_seconds=300,
        live_integrity={
            "is_live": True,
            "frame_advancing": True,
            "capture_advancing": True,
            "state_advancing": True,
            "source": "model_council",
            "cache_status": "fresh",
            "input_frame_hash": "frame-business-001",
            "previous_frame_hash": "frame-business-000",
            "packet_age_ms": 100,
        },
        model_council={
            "final_state": "EXECUTABLE",
            "final_side": "BUY",
            "decision_id": "mc-business-001",
            "maturity_stage": "EXECUTABLE_PACKET",
        },
        runtime_model_health={
            "all_required_models_awake": True,
            "council_status": "AWAKE",
            "max_model_latency_ms": 50,
            "queue_depth": 0,
        },
        sequence_context=complete_sequence_context_v3(
            sequence_id="seq-business-001",
            session_id="pocket-live-8788",
            side="BUY",
        ),
        allowance_package=_allowance_package(),
    )
    _deep_update(packet, updates)
    return packet


def _deep_update(target: dict[str, Any], updates: dict[str, Any]) -> None:
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_update(cast(dict[str, Any], target[key]), cast(dict[str, Any], value))
        else:
            target[key] = value


def test_tampered_command_rejected() -> None:
    signer = LocalEd25519Signer.local_test_key()
    result = build_connector_command(_packet(), account_state=_account(), signer=signer, now_epoch=NOW)
    assert result.accepted is True
    assert result.command["command_type"] == EXECUTION_COMMAND_TYPE

    tampered = deepcopy(result.command)
    tampered["execution"]["expiry_seconds"] = 60

    validation = validate_connector_command(tampered, signer=signer, now_epoch=NOW + 0.1)

    assert validation.rejected is True
    assert "INVALID_SIGNATURE_HASH" in validation.reason_codes or "INVALID_SIGNATURE" in validation.reason_codes


def test_expired_license_stops_executable_command() -> None:
    signer = LocalEd25519Signer.local_test_key()
    result = build_connector_command(
        _packet(),
        account_state=_account(license_expires_at_epoch_sec=NOW - 1.0),
        signer=signer,
        now_epoch=NOW,
    )

    assert result.rejected is True
    assert result.status_code == STATUS_LICENSE_EXPIRED
    assert result.command["command_type"] == STATUS_COMMAND_TYPE
    assert result.command["status"]["code"] == STATUS_LICENSE_EXPIRED
    assert result.command["execution"]["side"] == "NONE"
    assert not status_command_has_execution_authority(result.command)

    response = connector_poll_response(result)
    assert response["schema_version"] == "PG_CONNECTOR_COMMAND_RESPONSE_V1"
    assert response["executable"] is False


def test_duplicate_replayed_command_rejected() -> None:
    signer = LocalEd25519Signer.local_test_key()
    result = build_connector_command(_packet(), account_state=_account(), signer=signer, now_epoch=NOW)
    assert result.accepted is True

    ledger = CommandReplayLedger()
    first = validate_connector_command(result.command, signer=signer, now_epoch=NOW + 0.1, replay_ledger=ledger)
    second = validate_connector_command(result.command, signer=signer, now_epoch=NOW + 0.1, replay_ledger=ledger)

    assert first.accepted is True
    assert second.rejected is True
    assert "REPLAYED_COMMAND" in second.reason_codes


def test_stale_packet_rejected() -> None:
    signer = LocalEd25519Signer.local_test_key()
    packet = _packet(valid_until_epoch_sec=NOW - 0.1, valid_until_epoch=NOW - 0.1)

    result = build_connector_command(packet, account_state=_account(), signer=signer, now_epoch=NOW)

    assert result.rejected is True
    assert result.status_code == STATUS_NO_EXECUTION_PACKET
    assert "PACKET_EXPIRED" in result.reason_codes
    assert result.command["command_type"] == STATUS_COMMAND_TYPE
    assert not status_command_has_execution_authority(result.command)


def test_status_commands_never_contain_buy_or_sell_execution_authority() -> None:
    signer = LocalEd25519Signer.local_test_key()
    for status_code in (
        STATUS_NO_EXECUTION_PACKET,
        STATUS_LICENSE_EXPIRED,
        STATUS_DEVICE_REVOKED,
        STATUS_ACCOUNT_NOT_BOUND,
        STATUS_UPDATE_REQUIRED,
        STATUS_SERVICE_UNAVAILABLE,
    ):
        command = build_status_command(status_code, account_state=_account(), signer=signer, now_epoch=NOW)

        assert command["command_type"] == STATUS_COMMAND_TYPE
        assert not status_command_has_execution_authority(command)
        serialized = json.dumps(command, sort_keys=True)
        assert "BUY" not in serialized
        assert "SELL" not in serialized
