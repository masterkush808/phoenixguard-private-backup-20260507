from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
import base64
import hashlib
import hmac
import json
import time
from typing import Any, Mapping, cast

from phoenixguard.execution.packet_v3 import (
    PG_EXECUTION_PACKET_SCHEMA_VERSION,
    PacketValidationResult,
    format_expiry_text,
    packet_identity,
    validate_execution_packet_v3,
)

InvalidSignature: type[Exception]
serialization: Any | None
Ed25519PrivateKey: Any | None

try:  # pragma: no cover - exercised when cryptography is installed.
    from cryptography.exceptions import InvalidSignature as _InvalidSignature
    from cryptography.hazmat.primitives import serialization as _serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _Ed25519PrivateKey

    InvalidSignature = _InvalidSignature
    serialization = _serialization
    Ed25519PrivateKey = _Ed25519PrivateKey
    _has_ed25519 = True
except Exception:  # pragma: no cover - deterministic local fallback.
    InvalidSignature = Exception
    Ed25519PrivateKey = None
    serialization = None
    _has_ed25519 = False


MT4_EXECUTION_COMMAND_SCHEMA_VERSION = "PG_MT4_EXECUTION_COMMAND_V2"
CONNECTOR_COMMAND_RESPONSE_SCHEMA_VERSION = "PG_CONNECTOR_COMMAND_RESPONSE_V1"
CONNECTOR_ACK_SCHEMA_VERSION = "PG_CONNECTOR_COMMAND_ACK_V1"
SIGNATURE_SCHEMA_VERSION = "PG_COMMAND_SIGNATURE_V1"
EXECUTION_COMMAND_TYPE = "EXECUTION"
STATUS_COMMAND_TYPE = "STATUS"
EXECUTION_AUTHORITY = "PG_MODEL_COUNCIL_PACKET_AUTHORITY"
NO_EXECUTION_AUTHORITY = "NONE"
SIGNATURE_ALGORITHM = "ED25519"

STATUS_NO_EXECUTION_PACKET = "NO_EXECUTION_PACKET"
STATUS_LICENSE_EXPIRED = "LICENSE_EXPIRED"
STATUS_DEVICE_REVOKED = "DEVICE_REVOKED"
STATUS_ACCOUNT_NOT_BOUND = "ACCOUNT_NOT_BOUND"
STATUS_UPDATE_REQUIRED = "UPDATE_REQUIRED"
STATUS_SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
STATUS_CODES = frozenset(
    {
        STATUS_NO_EXECUTION_PACKET,
        STATUS_LICENSE_EXPIRED,
        STATUS_DEVICE_REVOKED,
        STATUS_ACCOUNT_NOT_BOUND,
        STATUS_UPDATE_REQUIRED,
        STATUS_SERVICE_UNAVAILABLE,
    }
)

_STATUS_MESSAGES = {
    STATUS_NO_EXECUTION_PACKET: "No executable packet is available.",
    STATUS_LICENSE_EXPIRED: "License is expired.",
    STATUS_DEVICE_REVOKED: "Device is revoked.",
    STATUS_ACCOUNT_NOT_BOUND: "Account is not bound.",
    STATUS_UPDATE_REQUIRED: "Connector update is required.",
    STATUS_SERVICE_UNAVAILABLE: "Command service is unavailable.",
}
_TEST_KEY_SEED_LABEL = b"phoenixguard.business.command_bridge.local_test_ed25519.v1"


@dataclass(frozen=True)
class ConnectorAccountState:
    license_id: str = "local-test-license"
    device_id: str = "local-test-device"
    account_id: str = "local-test-account"
    license_valid: bool = True
    license_expires_at_epoch_sec: float | None = None
    device_revoked: bool = False
    account_bound: bool = True
    update_required: bool = False
    service_available: bool = True

    @classmethod
    def local_test_active(cls, *, now_epoch: float | None = None, valid_for_seconds: float = 3600.0) -> "ConnectorAccountState":
        current = _now_epoch() if now_epoch is None else float(now_epoch)
        return cls(license_expires_at_epoch_sec=current + max(1.0, float(valid_for_seconds)))


@dataclass(frozen=True)
class CommandBuildResult:
    accepted: bool
    command: dict[str, Any]
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    status_code: str | None = None
    packet_validation: Mapping[str, Any] = field(default_factory=lambda: {})

    @property
    def executable(self) -> bool:
        return self.accepted and self.command.get("command_type") == EXECUTION_COMMAND_TYPE

    @property
    def rejected(self) -> bool:
        return not self.accepted

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "executable": self.executable,
            "rejected": self.rejected,
            "reason_codes": list(self.reason_codes),
            "status_code": self.status_code,
            "command": deepcopy(self.command),
            "packet_validation": dict(self.packet_validation),
        }


@dataclass(frozen=True)
class CommandValidationResult:
    accepted: bool
    reason_codes: tuple[str, ...] = field(default_factory=tuple)
    command_id: str | None = None
    command_type: str | None = None
    status_code: str | None = None

    @property
    def rejected(self) -> bool:
        return not self.accepted

    def as_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "rejected": self.rejected,
            "reason_codes": list(self.reason_codes),
            "command_id": self.command_id,
            "command_type": self.command_type,
            "status_code": self.status_code,
        }


class LocalEd25519Signer:
    """Deterministic local Ed25519 keypair for tests and offline connector mocks."""

    algorithm = SIGNATURE_ALGORITHM

    def __init__(self, seed: bytes, *, key_id: str = "pg-local-ed25519-test-v1") -> None:
        self.key_id = str(key_id or "pg-local-ed25519-test-v1")
        self._seed = _seed32(seed)
        private_key_cls = Ed25519PrivateKey
        self._private_key = private_key_cls.from_private_bytes(self._seed) if _has_ed25519 and private_key_cls is not None else None

    @classmethod
    def local_test_key(cls, *, key_id: str = "pg-local-ed25519-test-v1") -> "LocalEd25519Signer":
        return cls(hashlib.sha256(_TEST_KEY_SEED_LABEL).digest(), key_id=key_id)

    @classmethod
    def from_seed(cls, seed: bytes | str, *, key_id: str = "pg-local-ed25519-test-v1") -> "LocalEd25519Signer":
        raw = seed.encode("utf-8") if isinstance(seed, str) else bytes(seed)
        return cls(raw, key_id=key_id)

    @property
    def public_key_b64(self) -> str:
        serialization_module = cast(Any, serialization)
        if self._private_key is not None and serialization_module is not None:
            public_key = self._private_key.public_key()
            raw = public_key.public_bytes(
                encoding=serialization_module.Encoding.Raw,
                format=serialization_module.PublicFormat.Raw,
            )
        else:
            raw = hashlib.sha256(b"local-ed25519-public:" + self._seed).digest()
        return _b64(raw)

    def sign(self, payload: bytes) -> str:
        if self._private_key is not None:
            return _b64(self._private_key.sign(payload))
        return _b64(hmac.new(self._seed, payload, hashlib.sha256).digest())

    def verify(self, payload: bytes, signature_b64: str) -> bool:
        try:
            signature = base64.b64decode(signature_b64.encode("ascii"), validate=True)
        except Exception:
            return False
        if self._private_key is not None:
            try:
                self._private_key.public_key().verify(signature, payload)
                return True
            except InvalidSignature:
                return False
        expected = hmac.new(self._seed, payload, hashlib.sha256).digest()
        return hmac.compare_digest(signature, expected)

    def __repr__(self) -> str:
        return f"LocalEd25519Signer(key_id={self.key_id!r}, algorithm={self.algorithm!r})"


class CommandReplayLedger:
    def __init__(
        self,
        *,
        command_ids: set[str] | None = None,
        nonces: set[str] | None = None,
        packet_ids: set[str] | None = None,
    ) -> None:
        self._command_ids = set(command_ids or ())
        self._nonces = set(nonces or ())
        self._packet_ids = set(packet_ids or ())

    @property
    def command_ids(self) -> frozenset[str]:
        return frozenset(self._command_ids)

    @property
    def nonces(self) -> frozenset[str]:
        return frozenset(self._nonces)

    @property
    def packet_ids(self) -> frozenset[str]:
        return frozenset(self._packet_ids)

    def accept_packet(self, packet_id: str) -> bool:
        normalized = _clean_str(packet_id)
        if not normalized:
            return False
        if normalized in self._packet_ids:
            return False
        self._packet_ids.add(normalized)
        return True

    def accept_command(self, command_id: str, nonce: str) -> bool:
        normalized_command_id = _clean_str(command_id)
        normalized_nonce = _clean_str(nonce)
        if not normalized_command_id or not normalized_nonce:
            return False
        if normalized_command_id in self._command_ids or normalized_nonce in self._nonces:
            return False
        self._command_ids.add(normalized_command_id)
        self._nonces.add(normalized_nonce)
        return True


def build_connector_command(
    execution_packet: Mapping[str, Any] | None,
    *,
    account_state: ConnectorAccountState | None = None,
    signer: LocalEd25519Signer | None = None,
    now_epoch: float | None = None,
    command_ttl_seconds: float = 2.0,
    expected_session_id: str | None = None,
    expected_symbol: str | None = None,
    expected_timeframe: str | None = None,
    replay_ledger: CommandReplayLedger | None = None,
) -> CommandBuildResult:
    current = _now_epoch() if now_epoch is None else float(now_epoch)
    resolved_signer = signer or LocalEd25519Signer.local_test_key()
    account = account_state or ConnectorAccountState.local_test_active(now_epoch=current)

    account_blocker = connector_status_for_account(account, now_epoch=current)
    if account_blocker:
        command = build_status_command(account_blocker, account_state=account, signer=resolved_signer, now_epoch=current)
        return CommandBuildResult(False, command, (account_blocker,), account_blocker)

    if execution_packet is None:
        command = build_status_command(
            STATUS_NO_EXECUTION_PACKET,
            account_state=account,
            signer=resolved_signer,
            now_epoch=current,
        )
        return CommandBuildResult(False, command, (STATUS_NO_EXECUTION_PACKET,), STATUS_NO_EXECUTION_PACKET)

    validation = validate_execution_packet_v3(
        execution_packet,
        expected_session_id=expected_session_id,
        expected_symbol=expected_symbol,
        expected_timeframe=expected_timeframe,
        now_epoch=current,
        require_executable=True,
    )
    if not validation.ok or not validation.executable:
        reasons = validation.reason_codes or ("PACKET_NOT_EXECUTABLE",)
        command = build_status_command(
            STATUS_NO_EXECUTION_PACKET,
            account_state=account,
            signer=resolved_signer,
            now_epoch=current,
            detail_code=reasons[0],
        )
        return CommandBuildResult(False, command, reasons, STATUS_NO_EXECUTION_PACKET, validation.as_dict())

    professional_reasons = _professional_authority_rejection_reasons(execution_packet)
    if professional_reasons:
        command = build_status_command(
            STATUS_NO_EXECUTION_PACKET,
            account_state=account,
            signer=resolved_signer,
            now_epoch=current,
            detail_code=professional_reasons[0],
        )
        return CommandBuildResult(
            False,
            command,
            professional_reasons,
            STATUS_NO_EXECUTION_PACKET,
            validation.as_dict(),
        )

    packet_id = validation.packet_id or _clean_str(execution_packet.get("packet_id"))
    if replay_ledger is not None and not replay_ledger.accept_packet(packet_id):
        command = build_status_command(
            STATUS_NO_EXECUTION_PACKET,
            account_state=account,
            signer=resolved_signer,
            now_epoch=current,
            detail_code="DUPLICATE_EXECUTION_PACKET",
        )
        return CommandBuildResult(False, command, ("DUPLICATE_EXECUTION_PACKET",), STATUS_NO_EXECUTION_PACKET, validation.as_dict())

    unsigned = _build_unsigned_execution_command(
        execution_packet,
        validation=validation,
        account_state=account,
        signer=resolved_signer,
        now_epoch=current,
        command_ttl_seconds=command_ttl_seconds,
    )
    return CommandBuildResult(True, sign_command(unsigned, resolved_signer), (), None, validation.as_dict())


def build_status_command(
    status_code: str,
    *,
    account_state: ConnectorAccountState | None = None,
    signer: LocalEd25519Signer | None = None,
    now_epoch: float | None = None,
    detail_code: str | None = None,
    ttl_seconds: float = 10.0,
) -> dict[str, Any]:
    current = _now_epoch() if now_epoch is None else float(now_epoch)
    code = _normalize_status_code(status_code)
    account = account_state or ConnectorAccountState.local_test_active(now_epoch=current)
    resolved_signer = signer or LocalEd25519Signer.local_test_key()
    seed = f"status|{code}|{detail_code or ''}|{current:.6f}|{resolved_signer.key_id}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    unsigned: dict[str, Any] = {
        "schema_version": MT4_EXECUTION_COMMAND_SCHEMA_VERSION,
        "command_type": STATUS_COMMAND_TYPE,
        "command_id": f"pgstatus-{digest[:24]}",
        "nonce": digest[24:56],
        "issued_at_epoch_sec": current,
        "expires_at_epoch_sec": current + max(1.0, float(ttl_seconds)),
        "status": {
            "code": code,
            "detail_code": _clean_str(detail_code),
            "severity": "BLOCKED",
            "message": _STATUS_MESSAGES[code],
            "can_execute": False,
        },
        "execution": {
            "enabled": False,
            "authority": NO_EXECUTION_AUTHORITY,
            "side": "NONE",
            "expiry_seconds": 0,
            "amount_action": "DO_NOT_CHANGE_AMOUNT",
        },
        "connector_binding": _public_account_binding(account),
        "risk_controls": _status_risk_controls(),
        "connector_contract": _connector_contract_block(command_kind=STATUS_COMMAND_TYPE),
    }
    return sign_command(unsigned, resolved_signer)


def sign_command(command: Mapping[str, Any], signer: LocalEd25519Signer | None = None) -> dict[str, Any]:
    resolved_signer = signer or LocalEd25519Signer.local_test_key()
    unsigned = strip_command_signature(command)
    canonical = canonical_command_bytes(unsigned)
    signed = deepcopy(unsigned)
    signed["signature"] = {
        "schema_version": SIGNATURE_SCHEMA_VERSION,
        "algorithm": resolved_signer.algorithm,
        "key_id": resolved_signer.key_id,
        "public_key_fingerprint": _fingerprint(resolved_signer.public_key_b64),
        "payload_sha256": hashlib.sha256(canonical).hexdigest(),
        "value": resolved_signer.sign(canonical),
    }
    return signed


def validate_connector_command(
    command: Mapping[str, Any] | None,
    *,
    signer: LocalEd25519Signer | None = None,
    now_epoch: float | None = None,
    replay_ledger: CommandReplayLedger | None = None,
) -> CommandValidationResult:
    current = _now_epoch() if now_epoch is None else float(now_epoch)
    if not isinstance(command, Mapping):
        return CommandValidationResult(False, ("MISSING_COMMAND",))

    command_id = _clean_str(command.get("command_id")) or None
    command_type = _clean_str(command.get("command_type")).upper() or None
    status_code = _clean_str(_mapping(command.get("status")).get("code")) or None
    reasons: list[str] = []

    if command.get("schema_version") != MT4_EXECUTION_COMMAND_SCHEMA_VERSION:
        reasons.append("INVALID_COMMAND_SCHEMA")
    if command_type not in {EXECUTION_COMMAND_TYPE, STATUS_COMMAND_TYPE}:
        reasons.append("INVALID_COMMAND_TYPE")
    if not command_id:
        reasons.append("MISSING_COMMAND_ID")
    nonce = _clean_str(command.get("nonce"))
    if not nonce:
        reasons.append("MISSING_NONCE")

    issued_at = _float(command.get("issued_at_epoch_sec"), 0.0)
    expires_at = _float(command.get("expires_at_epoch_sec"), 0.0)
    if issued_at <= 0.0:
        reasons.append("MISSING_ISSUED_AT")
    if expires_at <= 0.0:
        reasons.append("MISSING_EXPIRES_AT")
    elif expires_at <= current:
        reasons.append("COMMAND_EXPIRED")

    signature = _mapping(command.get("signature"))
    signed_payload = canonical_command_bytes(strip_command_signature(command))
    payload_hash = hashlib.sha256(signed_payload).hexdigest()
    if not signature:
        reasons.append("MISSING_SIGNATURE")
    else:
        if signature.get("schema_version") != SIGNATURE_SCHEMA_VERSION:
            reasons.append("INVALID_SIGNATURE_SCHEMA")
        if signature.get("algorithm") != SIGNATURE_ALGORITHM:
            reasons.append("INVALID_SIGNATURE_ALGORITHM")
        if _clean_str(signature.get("payload_sha256")) != payload_hash:
            reasons.append("INVALID_SIGNATURE_HASH")
        resolved_signer = signer or LocalEd25519Signer.local_test_key()
        key_mismatch = (
            _clean_str(signature.get("key_id")) != resolved_signer.key_id
            or _clean_str(signature.get("public_key_fingerprint")) != _fingerprint(resolved_signer.public_key_b64)
        )
        if key_mismatch:
            reasons.append("SIGNING_KEY_MISMATCH")
        elif not resolved_signer.verify(signed_payload, _clean_str(signature.get("value"))):
            reasons.append("INVALID_SIGNATURE")

    execution = _mapping(command.get("execution"))
    if command_type == EXECUTION_COMMAND_TYPE:
        _validate_execution_authority(execution, reasons)
        _validate_strategy_authority(_mapping(command.get("strategy_authority")), execution, reasons)
    elif command_type == STATUS_COMMAND_TYPE:
        if status_code not in STATUS_CODES:
            reasons.append("INVALID_STATUS_CODE")
        if status_command_has_execution_authority(command):
            reasons.append("STATUS_CONTAINS_EXECUTION_AUTHORITY")

    if reasons:
        return CommandValidationResult(False, tuple(dict.fromkeys(reasons)), command_id, command_type, status_code)

    if replay_ledger is not None and not replay_ledger.accept_command(command_id or "", nonce):
        return CommandValidationResult(False, ("REPLAYED_COMMAND",), command_id, command_type, status_code)

    return CommandValidationResult(True, (), command_id, command_type, status_code)


def connector_status_for_account(account_state: ConnectorAccountState, *, now_epoch: float | None = None) -> str | None:
    current = _now_epoch() if now_epoch is None else float(now_epoch)
    if not account_state.service_available:
        return STATUS_SERVICE_UNAVAILABLE
    if account_state.update_required:
        return STATUS_UPDATE_REQUIRED
    if account_state.device_revoked:
        return STATUS_DEVICE_REVOKED
    if not account_state.account_bound:
        return STATUS_ACCOUNT_NOT_BOUND
    if not account_state.license_valid:
        return STATUS_LICENSE_EXPIRED
    expires_at = account_state.license_expires_at_epoch_sec
    if expires_at is not None and float(expires_at) <= current:
        return STATUS_LICENSE_EXPIRED
    return None


def connector_poll_response(result: CommandBuildResult) -> dict[str, Any]:
    return {
        "schema_version": CONNECTOR_COMMAND_RESPONSE_SCHEMA_VERSION,
        "command_available": bool(result.command),
        "executable": result.executable,
        "accepted": result.accepted,
        "status_code": result.status_code or "EXECUTION_COMMAND_READY",
        "reason_codes": list(result.reason_codes),
        "command": deepcopy(result.command),
    }


def connector_ack_payload(
    command: Mapping[str, Any],
    *,
    connector_id: str,
    accepted: bool,
    reason_codes: list[str] | tuple[str, ...] | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    current = _now_epoch() if now_epoch is None else float(now_epoch)
    return {
        "schema_version": CONNECTOR_ACK_SCHEMA_VERSION,
        "connector_id": _clean_str(connector_id),
        "command_id": _clean_str(command.get("command_id")),
        "command_type": _clean_str(command.get("command_type")).upper(),
        "accepted": bool(accepted),
        "reason_codes": list(reason_codes or ()),
        "observed_at_epoch_sec": current,
    }


def connector_contract_summary() -> dict[str, Any]:
    return {
        "command_schema_version": MT4_EXECUTION_COMMAND_SCHEMA_VERSION,
        "response_schema_version": CONNECTOR_COMMAND_RESPONSE_SCHEMA_VERSION,
        "ack_schema_version": CONNECTOR_ACK_SCHEMA_VERSION,
        "signature_schema_version": SIGNATURE_SCHEMA_VERSION,
        "signature_algorithm": SIGNATURE_ALGORITHM,
        "command_types": [EXECUTION_COMMAND_TYPE, STATUS_COMMAND_TYPE],
        "status_codes": sorted(STATUS_CODES),
        "execution_authority": EXECUTION_AUTHORITY,
        "status_execution_authority": NO_EXECUTION_AUTHORITY,
    }


def status_command_has_execution_authority(command: Mapping[str, Any]) -> bool:
    if _clean_str(command.get("command_type")).upper() != STATUS_COMMAND_TYPE:
        return False
    execution = _mapping(command.get("execution"))
    authority = _clean_str(execution.get("authority")).upper()
    side = _clean_str(execution.get("side")).upper()
    action = _clean_str(execution.get("action") or execution.get("execution_action")).upper()
    if execution.get("enabled") is True:
        return True
    return authority != NO_EXECUTION_AUTHORITY or side in {"BUY", "SELL"} or action in {"BUY", "SELL"}


def strip_command_signature(command: Mapping[str, Any]) -> dict[str, Any]:
    unsigned = deepcopy(dict(command))
    unsigned.pop("signature", None)
    return unsigned


def canonical_command_bytes(command: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(command),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")


def _allowance_package_from_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    council = _mapping(packet.get("model_council"))
    return _mapping(packet.get("allowance_package") or council.get("allowance_package"))


def _professional_trade_plan_from_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    allowance = _allowance_package_from_packet(packet)
    council = _mapping(packet.get("model_council"))
    return _mapping(
        allowance.get("professional_trade_plan")
        or packet.get("professional_trade_plan")
        or council.get("professional_trade_plan")
    )


def _professional_authority_rejection_reasons(packet: Mapping[str, Any]) -> tuple[str, ...]:
    allowance = _allowance_package_from_packet(packet)
    plan = _professional_trade_plan_from_packet(packet)
    reasons: list[str] = []
    if not plan:
        reasons.append("MISSING_PROFESSIONAL_TRADE_PLAN")
        return tuple(reasons)
    if plan.get("professional_grade") is not True:
        reasons.append("PROFESSIONAL_TRADE_PLAN_NOT_GRADE_READY")
    blocker = _clean_str(plan.get("blocker")).upper()
    if blocker not in {"", "NONE"}:
        reasons.append("PROFESSIONAL_TRADE_PLAN_BLOCKED")
    plan_side = _clean_str(plan.get("authority_side") or plan.get("side")).upper()
    allowance_side = _clean_str(allowance.get("side")).upper()
    if plan_side not in {"BUY", "SELL"}:
        reasons.append("PROFESSIONAL_TRADE_PLAN_SIDE_INVALID")
    elif allowance_side in {"BUY", "SELL"} and plan_side != allowance_side:
        reasons.append("PROFESSIONAL_TRADE_PLAN_SIDE_MISMATCH")
    horizon = _mapping(plan.get("thesis_horizon") or allowance.get("thesis_horizon"))
    expected_candles = _int(horizon.get("expected_candle_count"), 0)
    minimum_candles = max(1, _int(horizon.get("minimum_professional_candles"), 1))
    if expected_candles < minimum_candles:
        reasons.append("PROFESSIONAL_THESIS_HORIZON_TOO_SHORT")
    return tuple(dict.fromkeys(reasons))


def _professional_authority_ref(packet: Mapping[str, Any]) -> dict[str, Any]:
    allowance = _allowance_package_from_packet(packet)
    plan = _professional_trade_plan_from_packet(packet)
    horizon = _mapping(plan.get("thesis_horizon") or allowance.get("thesis_horizon"))
    resolution = _mapping(
        plan.get("professional_thesis_resolution")
        or allowance.get("professional_thesis_resolution")
        or packet.get("professional_thesis_resolution")
    )
    return {
        "schema_version": "PG_MT4_PROFESSIONAL_AUTHORITY_REF_V1",
        "execution_authority": _clean_str(allowance.get("execution_authority") or "PLAYBOOK_FINAL_DECIDER_V3"),
        "packet_authority": _clean_str(allowance.get("packet_authority") or PG_EXECUTION_PACKET_SCHEMA_VERSION),
        "professional_grade": bool(plan.get("professional_grade")),
        "side": _clean_str(plan.get("side") or allowance.get("side")).upper(),
        "authority_side": _clean_str(plan.get("authority_side") or plan.get("side") or allowance.get("side")).upper(),
        "thesis_class": _clean_str(plan.get("thesis_class")),
        "professional_thesis_state": _clean_str(plan.get("professional_thesis_state") or resolution.get("thesis_state")),
        "expected_duration_sec": _int(horizon.get("expected_duration_sec"), 0),
        "expected_candle_count": _int(horizon.get("expected_candle_count"), 0),
        "minimum_professional_candles": _int(horizon.get("minimum_professional_candles"), 0),
        "current_leg_candle_count": _int(horizon.get("current_leg_candle_count"), 0),
        "current_leg_side": _clean_str(horizon.get("current_leg_side")),
        "current_leg_stage": _clean_str(horizon.get("current_leg_stage")),
        "blocker": _clean_str(plan.get("blocker")),
        "next_required": _clean_str(plan.get("next_required")),
        "thesis_resolution": resolution,
    }


def _build_unsigned_execution_command(
    packet: Mapping[str, Any],
    *,
    validation: PacketValidationResult,
    account_state: ConnectorAccountState,
    signer: LocalEd25519Signer,
    now_epoch: float,
    command_ttl_seconds: float,
) -> dict[str, Any]:
    identity = packet_identity(packet)
    packet_id = validation.packet_id or _clean_str(packet.get("packet_id"))
    side = _clean_str(validation.side).upper()
    expiry_seconds = int(validation.expiry_seconds or 0)
    valid_until = _float(packet.get("valid_until_epoch_sec") or packet.get("valid_until_epoch"), now_epoch + command_ttl_seconds)
    expires_at = min(valid_until, now_epoch + max(0.1, float(command_ttl_seconds)))
    seed = f"execution|{packet_id}|{identity.get('frame_id')}|{identity.get('capture_count')}|{now_epoch:.6f}|{signer.key_id}"
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    command_id = f"pgcmd-{digest[:24]}"
    return {
        "schema_version": MT4_EXECUTION_COMMAND_SCHEMA_VERSION,
        "command_type": EXECUTION_COMMAND_TYPE,
        "command_id": command_id,
        "nonce": digest[24:56],
        "issued_at_epoch_sec": now_epoch,
        "expires_at_epoch_sec": expires_at,
        "packet_ref": _packet_ref(packet, validation=validation),
        "broker_binding": {
            "session_id": _clean_str(identity.get("session_id")),
            "symbol": _clean_str(identity.get("symbol")),
            "timeframe": _clean_str(identity.get("timeframe")).upper(),
        },
        "execution": {
            "enabled": True,
            "authority": EXECUTION_AUTHORITY,
            "side": side,
            "expiry_seconds": expiry_seconds,
            "expiry_text": format_expiry_text(expiry_seconds),
            "amount_action": "DO_NOT_CHANGE_AMOUNT",
            "risk_controls": _execution_risk_controls(),
        },
        "strategy_authority": _professional_authority_ref(packet),
        "connector_binding": _public_account_binding(account_state),
        "connector_contract": _connector_contract_block(command_kind=EXECUTION_COMMAND_TYPE),
    }


def _packet_ref(packet: Mapping[str, Any], *, validation: PacketValidationResult) -> dict[str, Any]:
    identity = packet_identity(packet)
    input_frame_hash = _clean_str(identity.get("input_frame_hash"))
    return {
        "packet_schema_version": PG_EXECUTION_PACKET_SCHEMA_VERSION,
        "packet_id": validation.packet_id or _clean_str(packet.get("packet_id")),
        "frame_id": _int(identity.get("frame_id")),
        "capture_count": _int(identity.get("capture_count")),
        "state_version": _int(identity.get("state_version")),
        "created_epoch_sec": _float(packet.get("created_epoch_sec") or packet.get("created_epoch"), 0.0),
        "valid_until_epoch_sec": _float(packet.get("valid_until_epoch_sec") or packet.get("valid_until_epoch"), 0.0),
        "input_frame_fingerprint": _fingerprint(input_frame_hash),
        "sequence_id": _clean_str(packet.get("sequence_id")),
    }


def _execution_risk_controls() -> dict[str, Any]:
    return {
        "ea_risk_controls_remain_authoritative": True,
        "ea_must_validate_command_signature": True,
        "ea_must_reject_replay": True,
        "ea_must_validate_symbol_timeframe": True,
        "ea_must_preserve_amount": True,
        "ea_must_enforce_local_risk_limits": True,
        "post_click_verification_required": True,
        "amount_override_allowed": False,
    }


def _status_risk_controls() -> dict[str, Any]:
    return {
        "ea_risk_controls_remain_authoritative": True,
        "ea_must_validate_command_signature": True,
        "ea_must_reject_replay": True,
        "executable_authority_present": False,
    }


def _connector_contract_block(*, command_kind: str) -> dict[str, Any]:
    return {
        "response_schema_version": CONNECTOR_COMMAND_RESPONSE_SCHEMA_VERSION,
        "ack_schema_version": CONNECTOR_ACK_SCHEMA_VERSION,
        "command_kind": _clean_str(command_kind).upper(),
        "ack_required": True,
        "replay_protection_required": True,
        "signature_required": True,
    }


def _validate_execution_authority(execution: Mapping[str, Any], reasons: list[str]) -> None:
    if execution.get("enabled") is not True:
        reasons.append("EXECUTION_NOT_ENABLED")
    if _clean_str(execution.get("authority")).upper() != EXECUTION_AUTHORITY:
        reasons.append("INVALID_EXECUTION_AUTHORITY")
    if _clean_str(execution.get("side")).upper() not in {"BUY", "SELL"}:
        reasons.append("INVALID_EXECUTION_SIDE")
    if _int(execution.get("expiry_seconds"), 0) <= 0:
        reasons.append("INVALID_EXPIRY_SECONDS")
    if execution.get("amount_action") != "DO_NOT_CHANGE_AMOUNT":
        reasons.append("AMOUNT_ACTION_NOT_LOCKED")
    risk_controls = _mapping(execution.get("risk_controls"))
    for field_name in (
        "ea_risk_controls_remain_authoritative",
        "ea_must_validate_command_signature",
        "ea_must_reject_replay",
        "ea_must_validate_symbol_timeframe",
        "ea_must_preserve_amount",
        "ea_must_enforce_local_risk_limits",
        "post_click_verification_required",
    ):
        if risk_controls.get(field_name) is not True:
            reasons.append("MISSING_EA_RISK_CONTROL")
            break
    if risk_controls.get("amount_override_allowed") is not False:
        reasons.append("AMOUNT_OVERRIDE_NOT_LOCKED")


def _validate_strategy_authority(strategy_authority: Mapping[str, Any], execution: Mapping[str, Any], reasons: list[str]) -> None:
    if not strategy_authority:
        reasons.append("MISSING_STRATEGY_AUTHORITY")
        return
    if strategy_authority.get("schema_version") != "PG_MT4_PROFESSIONAL_AUTHORITY_REF_V1":
        reasons.append("INVALID_STRATEGY_AUTHORITY_SCHEMA")
    if _clean_str(strategy_authority.get("execution_authority")).upper() != "PLAYBOOK_FINAL_DECIDER_V3":
        reasons.append("INVALID_STRATEGY_EXECUTION_AUTHORITY")
    if _clean_str(strategy_authority.get("packet_authority")).upper() != PG_EXECUTION_PACKET_SCHEMA_VERSION:
        reasons.append("INVALID_STRATEGY_PACKET_AUTHORITY")
    if strategy_authority.get("professional_grade") is not True:
        reasons.append("STRATEGY_NOT_PROFESSIONAL_GRADE")
    execution_side = _clean_str(execution.get("side")).upper()
    authority_side = _clean_str(strategy_authority.get("authority_side") or strategy_authority.get("side")).upper()
    if authority_side not in {"BUY", "SELL"}:
        reasons.append("INVALID_STRATEGY_AUTHORITY_SIDE")
    elif execution_side in {"BUY", "SELL"} and authority_side != execution_side:
        reasons.append("STRATEGY_SIDE_EXECUTION_MISMATCH")
    blocker = _clean_str(strategy_authority.get("blocker")).upper()
    if blocker not in {"", "NONE"}:
        reasons.append("STRATEGY_AUTHORITY_BLOCKED")
    expected_candles = _int(strategy_authority.get("expected_candle_count"), 0)
    minimum_candles = max(1, _int(strategy_authority.get("minimum_professional_candles"), 1))
    if expected_candles < minimum_candles:
        reasons.append("STRATEGY_THESIS_HORIZON_TOO_SHORT")


def _public_account_binding(account_state: ConnectorAccountState) -> dict[str, Any]:
    return {
        "license_fingerprint": _fingerprint(account_state.license_id),
        "device_fingerprint": _fingerprint(account_state.device_id),
        "account_fingerprint": _fingerprint(account_state.account_id),
    }


def _normalize_status_code(value: str) -> str:
    code = _clean_str(value).upper()
    if code not in STATUS_CODES:
        raise ValueError(f"unknown connector status code: {value!r}")
    return code


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _clean_str(value: Any) -> str:
    return str(value or "").strip()


def _float(value: Any, default: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(default)
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return float(default)
    return float(parsed)


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return int(default)


def _fingerprint(value: Any) -> str:
    text = _clean_str(value)
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _seed32(seed: bytes) -> bytes:
    return seed if len(seed) == 32 else hashlib.sha256(seed).digest()


def _b64(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _now_epoch() -> float:
    return float(time.time())


__all__ = [
    "CONNECTOR_ACK_SCHEMA_VERSION",
    "CONNECTOR_COMMAND_RESPONSE_SCHEMA_VERSION",
    "EXECUTION_AUTHORITY",
    "EXECUTION_COMMAND_TYPE",
    "MT4_EXECUTION_COMMAND_SCHEMA_VERSION",
    "NO_EXECUTION_AUTHORITY",
    "SIGNATURE_ALGORITHM",
    "SIGNATURE_SCHEMA_VERSION",
    "STATUS_ACCOUNT_NOT_BOUND",
    "STATUS_CODES",
    "STATUS_DEVICE_REVOKED",
    "STATUS_LICENSE_EXPIRED",
    "STATUS_NO_EXECUTION_PACKET",
    "STATUS_SERVICE_UNAVAILABLE",
    "STATUS_UPDATE_REQUIRED",
    "STATUS_COMMAND_TYPE",
    "CommandBuildResult",
    "CommandReplayLedger",
    "CommandValidationResult",
    "ConnectorAccountState",
    "LocalEd25519Signer",
    "build_connector_command",
    "build_status_command",
    "canonical_command_bytes",
    "connector_ack_payload",
    "connector_contract_summary",
    "connector_poll_response",
    "connector_status_for_account",
    "sign_command",
    "status_command_has_execution_authority",
    "strip_command_signature",
    "validate_connector_command",
]
