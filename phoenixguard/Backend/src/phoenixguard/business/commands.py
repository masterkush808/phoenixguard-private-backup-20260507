from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field
from typing import Any, Mapping

InvalidSignature: type[Exception]
Ed25519PrivateKey: Any | None
Encoding: Any | None
PublicFormat: Any | None

try:  # pragma: no cover - fallback exists for lean local Python installs.
    from cryptography.exceptions import InvalidSignature as _InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey as _Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding as _Encoding, PublicFormat as _PublicFormat

    InvalidSignature = _InvalidSignature
    Ed25519PrivateKey = _Ed25519PrivateKey
    Encoding = _Encoding
    PublicFormat = _PublicFormat
except Exception:  # pragma: no cover
    InvalidSignature = Exception
    Ed25519PrivateKey = None
    Encoding = None
    PublicFormat = None


EXECUTION_STATUS = "EXECUTION_PACKET"
STATUS_COMMANDS = frozenset(
    {
        "NO_EXECUTION_PACKET",
        "LICENSE_EXPIRED",
        "DEVICE_REVOKED",
        "ACCOUNT_NOT_BOUND",
        "UPDATE_REQUIRED",
        "SERVICE_UNAVAILABLE",
    }
)
EXECUTION_SIDES = frozenset({"BUY", "SELL"})


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}".encode("ascii"))


def command_hash(command: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(command)).hexdigest()


@dataclass(frozen=True)
class CommandSigner:
    """Test-mode Ed25519-style signer for connector command envelopes."""

    key_id: str = "pg-test-ed25519-2026-06"
    seed: bytes = field(default_factory=lambda: hashlib.sha256(b"phoenixguard-business-mock-key").digest())

    @property
    def algorithm(self) -> str:
        return "ed25519-test" if Ed25519PrivateKey is not None else "hmac-sha256-test"

    def _private_key(self) -> Any:
        if Ed25519PrivateKey is None:
            return None
        return Ed25519PrivateKey.from_private_bytes(self.seed[:32])

    def public_key_b64(self) -> str:
        private_key = self._private_key()
        if private_key is None or Encoding is None or PublicFormat is None:
            return _b64url(hashlib.sha256(self.seed).digest())
        public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        return _b64url(public_key)

    def sign(self, command: Mapping[str, Any]) -> dict[str, Any]:
        unsigned = dict(command)
        unsigned.pop("signature", None)
        unsigned.pop("signature_alg", None)
        unsigned.pop("key_id", None)
        unsigned.pop("public_key", None)
        body = _canonical_json(unsigned)
        private_key = self._private_key()
        if private_key is None:
            signature = hmac.new(self.seed, body, hashlib.sha256).digest()
        else:
            signature = private_key.sign(body)
        signed = dict(unsigned)
        signed.update(
            {
                "signature": _b64url(signature),
                "signature_alg": self.algorithm,
                "key_id": self.key_id,
                "public_key": self.public_key_b64(),
            }
        )
        return signed

    def verify(self, signed_command: Mapping[str, Any]) -> bool:
        signature = str(signed_command.get("signature") or "")
        if not signature:
            return False
        unsigned = dict(signed_command)
        unsigned.pop("signature", None)
        unsigned.pop("signature_alg", None)
        unsigned.pop("key_id", None)
        unsigned.pop("public_key", None)
        body = _canonical_json(unsigned)
        signature_bytes = _b64url_decode(signature)
        private_key = self._private_key()
        if private_key is None:
            expected = hmac.new(self.seed, body, hashlib.sha256).digest()
            return hmac.compare_digest(signature_bytes, expected)
        try:
            public_key = private_key.public_key()
            public_key.verify(signature_bytes, body)
        except InvalidSignature:
            return False
        return True


class CommandReplayGuard:
    def __init__(self) -> None:
        self._seen: set[str] = set()

    def accept_once(self, command: Mapping[str, Any]) -> bool:
        packet_id = str(command.get("packet_id") or command.get("command_id") or "")
        if not packet_id:
            return False
        if packet_id in self._seen:
            return False
        self._seen.add(packet_id)
        return True


def build_status_command(
    status: str,
    *,
    reason: str = "",
    now_epoch: float | None = None,
    packet_id: str | None = None,
) -> dict[str, Any]:
    normalized = str(status or "").strip().upper()
    if normalized not in STATUS_COMMANDS:
        raise ValueError(f"Unsupported connector status command: {status}")
    created = float(now_epoch if now_epoch is not None else time.time())
    command: dict[str, Any] = {
        "schema_version": "PG_MT4_EXECUTION_COMMAND_V2",
        "status": normalized,
        "packet_id": packet_id or f"status-{normalized.lower()}-{int(created * 1000)}",
        "created_epoch": created,
        "valid_until_epoch": created + 15.0,
        "execution_authority": False,
        "reason": reason,
    }
    return command


def build_execution_command(
    internal_packet: Mapping[str, Any],
    *,
    license_id: str,
    device_id: str,
    signer: CommandSigner | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    now = float(now_epoch if now_epoch is not None else time.time())
    valid_until = float(internal_packet.get("valid_until_epoch") or internal_packet.get("expires_epoch") or now + 8.0)
    if valid_until <= now:
        raise ValueError("Execution packet is stale.")
    side = str(internal_packet.get("side") or internal_packet.get("action") or "").strip().upper()
    if side not in EXECUTION_SIDES:
        raise ValueError("Executable command requires BUY or SELL side.")
    symbol = str(internal_packet.get("symbol") or "EURUSD").strip().upper()
    command: dict[str, Any] = {
        "schema_version": "PG_MT4_EXECUTION_COMMAND_V2",
        "status": EXECUTION_STATUS,
        "packet_id": str(internal_packet.get("packet_id") or f"mock-{int(now * 1000)}"),
        "stream_sequence": int(internal_packet.get("stream_sequence") or 1),
        "license_id": license_id,
        "device_id": device_id,
        "side": side,
        "symbol": symbol,
        "timeframe": str(internal_packet.get("timeframe") or "M1").strip().upper(),
        "confidence": float(internal_packet.get("confidence") or 0.74),
        "created_epoch": now,
        "valid_until_epoch": valid_until,
        "execution_authority": True,
        "risk_controls": {
            "user_controlled_risk": True,
            "broker_password_required": False,
            "max_duration_seconds": int(internal_packet.get("expiry_seconds") or 900),
        },
    }
    command["command_hash"] = command_hash(command)
    return (signer or CommandSigner()).sign(command)


def verify_signed_command(
    signed_command: Mapping[str, Any],
    *,
    signer: CommandSigner | None = None,
    replay_guard: CommandReplayGuard | None = None,
    now_epoch: float | None = None,
) -> bool:
    status = str(signed_command.get("status") or "").strip().upper()
    if status == EXECUTION_STATUS and str(signed_command.get("side") or "").strip().upper() not in EXECUTION_SIDES:
        return False
    if status in STATUS_COMMANDS and str(signed_command.get("side") or "").strip().upper() in EXECUTION_SIDES:
        return False
    valid_until = float(signed_command.get("valid_until_epoch") or 0.0)
    now = float(now_epoch if now_epoch is not None else time.time())
    if valid_until and valid_until <= now:
        return False
    if not (signer or CommandSigner()).verify(signed_command):
        return False
    if replay_guard is not None and not replay_guard.accept_once(signed_command):
        return False
    return True


def latest_command_for_context(
    *,
    entitlement_status: str,
    license_id: str,
    device_id: str,
    account_bound: bool,
    device_status: str = "active",
    update_required: bool = False,
    internal_packet: Mapping[str, Any] | None = None,
    signer: CommandSigner | None = None,
    now_epoch: float | None = None,
) -> dict[str, Any]:
    if device_status != "active":
        return {"status": "DEVICE_REVOKED", "command": build_status_command("DEVICE_REVOKED", reason="Device is not active.")}
    if entitlement_status not in {"active", "trialing", "grace"}:
        return {"status": "LICENSE_EXPIRED", "command": build_status_command("LICENSE_EXPIRED", reason="License is not active.")}
    if not account_bound:
        return {"status": "ACCOUNT_NOT_BOUND", "command": build_status_command("ACCOUNT_NOT_BOUND", reason="Bind a broker account before command delivery.")}
    if update_required:
        return {"status": "UPDATE_REQUIRED", "command": build_status_command("UPDATE_REQUIRED", reason="Connector update is required.")}
    if internal_packet is None:
        return {"status": "NO_EXECUTION_PACKET", "command": build_status_command("NO_EXECUTION_PACKET", reason="No approved execution packet is available.")}
    try:
        command = build_execution_command(
            internal_packet,
            license_id=license_id,
            device_id=device_id,
            signer=signer,
            now_epoch=now_epoch,
        )
    except ValueError as exc:
        return {"status": "SERVICE_UNAVAILABLE", "command": build_status_command("SERVICE_UNAVAILABLE", reason=str(exc))}
    return {"status": EXECUTION_STATUS, "command": command}
