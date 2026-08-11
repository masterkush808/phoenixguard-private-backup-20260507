from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
import hmac
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Protocol, Sequence, cast

from fastapi import APIRouter, Body, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse
from PIL import Image
from starlette.concurrency import run_in_threadpool


DEFAULT_MAX_FRAME_BYTES = 15 * 1024 * 1024
DEFAULT_MAX_FRAME_PIXELS = 36_000_000
DEFAULT_MIN_FRAME_SIDE = 64
DEFAULT_MAX_FRAME_SIDE = 8192
DEFAULT_MAX_METADATA_BYTES = 8192
DEFAULT_MIN_INTERVAL_SEC = 10
DEFAULT_ACTIVE_FEED_WINDOW_SEC = 240
DEFAULT_MAX_ACTIVE_FEEDS_TOTAL = 3
DEFAULT_MAX_ACTIVE_FEEDS_PER_TOKEN = 1
DEFAULT_MAX_ANALYSIS_MAILBOX_STATES = 64
DEFAULT_SIGNATURE_MAX_SKEW_SEC = 300
DEFAULT_SIGNATURE_NONCE_TTL_SEC = 600
SUPPORTED_FRAME_SOURCE_TYPES = (
    "windows_window_capture",
    "windows_graphics_capture_roi",
    "edge_agent_screenshot",
    "pc_screen_capture",
    "mobile_manual_upload",
    "mobile_pwa_screen_capture",
    "android_native_capture",
    "ios_replaykit_capture",
    "browser_extension_capture",
    "browser_tab_roi_capture",
    "cloud_browser_worker",
    "mt4_chart_screenshot",
    "replay_upload",
)
_SERVER_METADATA_FIELDS = (
    "client_host",
    "filename",
    "feed_token_name",
    "feed_user_id",
    "frame_sha256",
    "frame_bytes",
    "source_generation",
    "source_lease_id",
)
BROWSER_EXTENSION_COORDINATE_SPACES = (
    "edge_tab_content_v1",
    "edge_tab_roi_v1",
)
LEASED_COORDINATE_SPACES = frozenset({"edge_tab_roi_v1", "wgc_hwnd_roi_v1"})
VISUAL_IDENTITY_SOURCE_CONTRACTS = frozenset(
    {
        ("browser_tab_roi_capture", "edge_tab_roi_v1"),
        ("windows_graphics_capture_roi", "wgc_hwnd_roi_v1"),
    }
)


def _effective_frame_identity_hints(
    symbol: str,
    timeframe: str,
    metadata: Mapping[str, Any],
) -> tuple[str, str, bool]:
    """Discard persisted identity hints for adaptive leased chart regions."""

    contract = (
        str(metadata.get("source_type", "") or "").strip(),
        str(metadata.get("coordinate_space", "") or "").strip(),
    )
    ignored = bool(
        contract in VISUAL_IDENTITY_SOURCE_CONTRACTS
        and (str(symbol or "").strip() or str(timeframe or "").strip())
    )
    if contract in VISUAL_IDENTITY_SOURCE_CONTRACTS:
        return "", "", ignored
    return str(symbol or ""), str(timeframe or ""), False


@dataclass(frozen=True, slots=True)
class FeedAuthContext:
    token_name: str
    user_id: str
    global_token: bool
    signing_secret: str = ""
    allowed_session_prefixes: tuple[str, ...] = ()
    allowed_source_ids: tuple[str, ...] = ()
    allowed_symbols: tuple[str, ...] = ()
    allowed_timeframes: tuple[str, ...] = ()
    max_active_feeds: int = DEFAULT_MAX_ACTIVE_FEEDS_PER_TOKEN
    min_interval_sec: int = DEFAULT_MIN_INTERVAL_SEC


@dataclass(slots=True)
class FeedRuntimeState:
    token_name: str
    user_id: str
    session_id: str
    source_id: str
    sequence_id: str
    source_generation: int
    last_capture_epoch_ms: int
    last_frame_id: int
    last_seen_monotonic: float


@dataclass(frozen=True, slots=True)
class FeedRuntimeReservation:
    key: str
    current: FeedRuntimeState
    previous: FeedRuntimeState | None
    retired_states: tuple[tuple[str, FeedRuntimeState], ...] = ()


@dataclass(slots=True)
class FrameAnalysisJob:
    session_id: str
    image: Image.Image
    source_id: str
    symbol: str
    timeframe: str
    source_url: str
    sequence_id: str
    capture_epoch_ms: int
    frame_id: int
    source_generation: int
    source_lease_id: str
    metadata: dict[str, Any]
    tracker: Any
    audit_context: FeedAuthContext
    audit_fields: dict[str, object]
    identity_key: tuple[str, ...]
    mailbox_epoch: int = 0


@dataclass(frozen=True, slots=True)
class FrameAnalysisOutcome:
    state: str
    result: dict[str, Any] | None = None
    reason_code: str = ""
    error_type: str = ""


@dataclass(slots=True)
class FrameAnalysisMailbox:
    session_id: str
    active_job: FrameAnalysisJob | None = None
    pending_job: FrameAnalysisJob | None = None
    worker: threading.Thread | None = None
    epoch: int = 0
    replaced_frame_count: int = 0
    retired: bool = False
    identity_key: tuple[str, ...] = ()
    last_result: dict[str, Any] | None = None
    retry_after_ms: int = 0
    last_completed_frame_id: int | None = None
    last_completed_epoch_ms: int = 0
    last_failed_frame_id: int | None = None
    last_failed_epoch_ms: int = 0
    last_failure_reason_code: str = ""
    last_failure_error_type: str = ""
    last_touched_monotonic: float = 0.0


_REPO_ROOT = Path(__file__).resolve().parents[4]
_MOBILE_UPLOADER_PATH = _REPO_ROOT / "Frontend" / "dashboard" / "static" / "frame_ingest" / "mobile_frame_uploader.html"
_FEED_STATE_LOCK = threading.Lock()
_FEED_RUNTIME_STATE: dict[str, FeedRuntimeState] = {}
_ANALYSIS_MAILBOX_LOCK = threading.Lock()
_ANALYSIS_MAILBOXES: dict[str, FrameAnalysisMailbox] = {}
_SIGNATURE_NONCE_LOCK = threading.Lock()
_SIGNATURE_NONCES: dict[str, float] = {}
_SECURITY_AUDIT_LOCK = threading.Lock()


def reset_frame_ingest_runtime_state_for_tests() -> None:
    with _FEED_STATE_LOCK:
        _FEED_RUNTIME_STATE.clear()
    with _ANALYSIS_MAILBOX_LOCK:
        mailboxes = list(_ANALYSIS_MAILBOXES.values())
        _ANALYSIS_MAILBOXES.clear()
        for mailbox in mailboxes:
            mailbox.retired = True
            mailbox.epoch += 1
            pending = mailbox.pending_job
            mailbox.pending_job = None
            if pending is not None:
                pending.image.close()
    with _SIGNATURE_NONCE_LOCK:
        _SIGNATURE_NONCES.clear()


class FrameIngestTracker(Protocol):
    def ingest_external_frame(
        self,
        session_id: str,
        image: Image.Image,
        *,
        source_id: str,
        symbol: str = "",
        timeframe: str = "",
        source_url: str = "",
        sequence_id: str = "",
        capture_epoch_ms: int = 0,
        frame_id: int = 0,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def get_session_snapshot(self, session_id: str) -> dict[str, Any]:
        ...

    def get_external_frame_transport_status(
        self,
        session_id: str,
    ) -> dict[str, Any]:
        ...

    def fail_external_frame_analysis(
        self,
        session_id: str,
        *,
        source_id: str,
        sequence_id: str,
        source_generation: int,
        source_lease_id: str,
        source_type: str,
        coordinate_space: str,
        capture_epoch_ms: int,
        frame_id: int,
        reason_code: str,
        error_type: str,
    ) -> dict[str, Any]:
        ...

    def claim_external_source(
        self,
        session_id: str,
        *,
        source_id: str,
        sequence_id: str,
        source_type: str,
        selection_id: str,
        display_name: str,
        coordinate_space: str,
        expected_source_control: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def validate_external_source_lease(
        self,
        session_id: str,
        *,
        source_id: str,
        sequence_id: str,
        source_generation: int,
        source_lease_id: str,
    ) -> dict[str, Any]:
        ...

    def heartbeat_external_source(
        self,
        session_id: str,
        *,
        source_id: str,
        sequence_id: str,
        source_generation: int,
        source_lease_id: str,
        capture_epoch_ms: int,
        source_render_fresh: bool,
        material_change_pending: bool = False,
        roi_normalized: Any = None,
        roi_source_pixels: Mapping[str, Any] | None = None,
        source_surface_width: int = 0,
        source_surface_height: int = 0,
        transport_frame_age_ms: int = 0,
        decoder_frame_age_ms: int = 0,
        capture_health_reason: str = "",
        capture_status: str = "",
        presented_frames: int = 0,
        media_time: float = 0.0,
        identity_observation_v3: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        ...

    def kill_external_source(
        self,
        session_id: str,
        *,
        reason: str,
        source_id: str = "",
        sequence_id: str = "",
        source_generation: int = 0,
        source_lease_id: str = "",
    ) -> dict[str, Any]:
        ...


def _env_int(name: str, default: int, minimum: int) -> int:
    raw = os.getenv(name, str(default)) or str(default)
    try:
        parsed = int(raw)
    except ValueError:
        parsed = int(default)
    return max(int(minimum), int(parsed))


def _env_bool(name: str, default: bool) -> bool:
    raw = str(os.getenv(name, "1" if default else "0") or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _env_csv(name: str) -> tuple[str, ...]:
    raw = str(os.getenv(name, "") or "").strip()
    if not raw:
        return ()
    return tuple(part.strip() for part in raw.split(",") if part.strip())


def _configured_ingest_token() -> str:
    return str(os.getenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "") or "").strip()


def _token_registry_path() -> Path | None:
    raw_path = str(os.getenv("PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY", "") or "").strip()
    if not raw_path:
        return None
    return Path(raw_path).expanduser()


def _tuple_from_raw(value: object, *, upper: bool = False) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list | tuple):
        sequence = cast(Sequence[object], value)
        items = [str(item) for item in sequence]
    else:
        items = [str(value)]
    cleaned: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if not text:
            continue
        cleaned.append(text.upper() if upper else text)
    return tuple(cleaned)


def _load_token_registry() -> list[Mapping[str, Any]]:
    path = _token_registry_path()
    if path is None:
        return []
    if not path.exists():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Frame ingest token registry file is missing.")
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Frame ingest token registry is not readable JSON.") from exc
    if not isinstance(parsed, Mapping):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Frame ingest token registry must be a JSON object.")
    parsed_mapping = cast(Mapping[str, object], parsed)
    tokens_obj = parsed_mapping.get("tokens", [])
    if not isinstance(tokens_obj, list):
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Frame ingest token registry tokens must be a list.")
    tokens = cast(list[object], tokens_obj)
    return [cast(Mapping[str, Any], token) for token in tokens if isinstance(token, Mapping)]


def _rule_secret(rule: Mapping[str, Any]) -> str:
    token_env = str(rule.get("token_env") or "").strip()
    if token_env:
        return str(os.getenv(token_env, "") or "").strip()
    return str(rule.get("token") or "").strip()


def _rule_signing_secret(rule: Mapping[str, Any]) -> str:
    secret_env = str(rule.get("signing_secret_env") or "").strip()
    if secret_env:
        return str(os.getenv(secret_env, "") or "").strip()
    return str(rule.get("signing_secret") or "").strip()


def _rule_matches_token(rule: Mapping[str, Any], supplied: str) -> bool:
    if not bool(rule.get("enabled", True)):
        return False
    expected = _rule_secret(rule)
    if expected and secrets.compare_digest(supplied, expected):
        return True
    token_sha256 = str(rule.get("token_sha256") or "").strip().lower()
    if not token_sha256:
        return False
    supplied_hash = hashlib.sha256(supplied.encode("utf-8")).hexdigest()
    return secrets.compare_digest(supplied_hash, token_sha256)


def _context_from_rule(rule: Mapping[str, Any]) -> FeedAuthContext:
    max_active_feeds = _env_int(
        "PHOENIXGUARD_FRAME_INGEST_MAX_ACTIVE_FEEDS_PER_TOKEN",
        DEFAULT_MAX_ACTIVE_FEEDS_PER_TOKEN,
        1,
    )
    min_interval_sec = _env_int("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", DEFAULT_MIN_INTERVAL_SEC, 1)
    return FeedAuthContext(
        token_name=str(rule.get("name") or "scoped-feed-token").strip() or "scoped-feed-token",
        user_id=str(rule.get("user_id") or "").strip(),
        global_token=False,
        signing_secret=_rule_signing_secret(rule),
        allowed_session_prefixes=_tuple_from_raw(rule.get("allowed_session_prefixes")),
        allowed_source_ids=_tuple_from_raw(rule.get("allowed_source_ids")),
        allowed_symbols=_tuple_from_raw(rule.get("allowed_symbols"), upper=True),
        allowed_timeframes=_tuple_from_raw(rule.get("allowed_timeframes"), upper=True),
        max_active_feeds=_env_int("PHOENIXGUARD_FRAME_INGEST_RULE_MAX_ACTIVE_FEEDS", int(rule.get("max_active_feeds") or max_active_feeds), 1),
        min_interval_sec=_env_int("PHOENIXGUARD_FRAME_INGEST_RULE_MIN_INTERVAL_SEC", int(rule.get("min_interval_sec") or min_interval_sec), 1),
    )


def _submitted_token(authorization: str | None, x_phoenixguard_token: str | None) -> str:
    auth = str(authorization or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(x_phoenixguard_token or "").strip()


def _require_ingest_token(authorization: str | None, x_phoenixguard_token: str | None) -> FeedAuthContext:
    expected = _configured_ingest_token()
    rules = _load_token_registry()
    if not expected:
        if not rules:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Frame ingest is not armed. Set PHOENIXGUARD_FRAME_INGEST_TOKEN or PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY before exposing this endpoint.",
            )
    supplied = _submitted_token(authorization, x_phoenixguard_token)
    if not supplied:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid frame ingest token.")
    if expected and secrets.compare_digest(supplied, expected):
        return FeedAuthContext(
            token_name="global",
            user_id="",
            global_token=True,
            signing_secret=str(os.getenv("PHOENIXGUARD_FRAME_INGEST_SIGNING_SECRET", "") or "").strip(),
            max_active_feeds=_env_int(
                "PHOENIXGUARD_FRAME_INGEST_MAX_ACTIVE_FEEDS_PER_TOKEN",
                DEFAULT_MAX_ACTIVE_FEEDS_PER_TOKEN,
                1,
            ),
            min_interval_sec=_env_int("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", DEFAULT_MIN_INTERVAL_SEC, 1),
        )
    for rule in rules:
        if _rule_matches_token(rule, supplied):
            return _context_from_rule(rule)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid frame ingest token.")


def _security_audit_log_path() -> Path:
    raw_path = str(os.getenv("PHOENIXGUARD_SECURITY_AUDIT_LOG", "") or "").strip()
    if raw_path:
        return Path(raw_path).expanduser()
    return _REPO_ROOT / "runtime" / "live" / "logs_live" / "security_audit.jsonl"


def _security_audit(event: str, context: FeedAuthContext | None, fields: Mapping[str, object]) -> None:
    if not _env_bool("PHOENIXGUARD_SECURITY_AUDIT_LOG_ENABLED", True):
        return
    payload: dict[str, object] = {
        "schema_version": "PG_SECURITY_AUDIT_V1",
        "event": event,
        "epoch_ms": int(round(time.time() * 1000.0)),
        "token_name": context.token_name if context is not None else "",
        "user_id": context.user_id if context is not None else "",
    }
    payload.update({str(key): value for key, value in fields.items()})
    try:
        path = _security_audit_log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(payload, separators=(",", ":"), sort_keys=True)
        with _SECURITY_AUDIT_LOCK:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
    except OSError:
        # Audit logging must never make the live tracker unavailable. The
        # watchdog/readiness checks validate log-path configuration before prod.
        return


def _analysis_audit_context(context: FeedAuthContext) -> FeedAuthContext:
    """Keep only public audit identity on work that outlives the HTTP request."""

    return FeedAuthContext(
        token_name=context.token_name,
        user_id=context.user_id,
        global_token=context.global_token,
    )


def _analysis_identity_key(
    *,
    source_id: str,
    sequence_id: str,
    source_generation: int,
    symbol: str,
    timeframe: str,
    metadata: Mapping[str, Any],
) -> tuple[str, ...]:
    return (
        str(source_id or "").strip(),
        str(sequence_id or "").strip(),
        str(max(0, int(source_generation or 0))),
        str(symbol or "").strip().upper(),
        str(timeframe or "").strip().upper(),
        str(metadata.get("source_type", "") or "").strip(),
        str(metadata.get("coordinate_space", "") or "").strip(),
    )


def _bounded_analysis_result(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    source = cast(Mapping[str, Any], value)
    external_value = source.get("external_frame_feed", {})
    external_source: Mapping[str, Any] = (
        cast(Mapping[str, Any], external_value)
        if isinstance(external_value, Mapping)
        else cast(Mapping[str, Any], {})
    )
    external_frame_feed: dict[str, Any] = {}
    for key in (
            "source_id",
            "source_type",
            "sequence_id",
            "symbol",
            "timeframe",
            "frame_id",
            "capture_epoch_ms",
            "source_generation",
            "coordinate_space",
    ):
        if key in external_source:
            external_frame_feed[key] = external_source.get(key)
    return {
        "session_id": str(source.get("session_id", "") or ""),
        "status": str(source.get("status", "") or ""),
        "capture_count": max(0, int(source.get("capture_count", 0) or 0)),
        "frame_index": max(0, int(source.get("frame_index", 0) or 0)),
        "state_version": max(0, int(source.get("state_version", 0) or 0)),
        "decision_version": max(0, int(source.get("decision_version", 0) or 0)),
        "external_frame_feed": external_frame_feed,
    }


def _public_analysis_mailbox_locked(
    mailbox: FrameAnalysisMailbox | None,
    *,
    default_retry_after_ms: int = 0,
) -> dict[str, object]:
    active = mailbox.active_job if mailbox is not None and not mailbox.retired else None
    pending = mailbox.pending_job if mailbox is not None and not mailbox.retired else None
    active_superseded = bool(
        active is not None
        and mailbox is not None
        and (
            active.mailbox_epoch != mailbox.epoch
            or active.identity_key != mailbox.identity_key
        )
    )
    retry_after_ms = (
        int(mailbox.retry_after_ms)
        if mailbox is not None
        else int(default_retry_after_ms or 0)
    )
    return {
        "schema_version": "PG_FRAME_ANALYSIS_MAILBOX_V1",
        "analysis_busy": active is not None,
        "active_frame_id": int(active.frame_id) if active is not None else None,
        "active_superseded": active_superseded,
        "pending_frame_id": int(pending.frame_id) if pending is not None else None,
        "replaced_frame_count": min(
            2_147_483_647,
            max(0, int(mailbox.replaced_frame_count if mailbox is not None else 0)),
        ),
        "retry_after_ms": min(300_000, max(0, retry_after_ms)),
        "max_active_jobs": 1,
        "max_pending_jobs": 1,
        "latest_pending_replaces": True,
        "last_completed_frame_id": (
            mailbox.last_completed_frame_id if mailbox is not None else None
        ),
        "last_completed_epoch_ms": (
            max(0, int(mailbox.last_completed_epoch_ms))
            if mailbox is not None
            else 0
        ),
        "last_failed_frame_id": (
            mailbox.last_failed_frame_id if mailbox is not None else None
        ),
        "last_failed_epoch_ms": (
            max(0, int(mailbox.last_failed_epoch_ms))
            if mailbox is not None
            else 0
        ),
        "last_failure_reason_code": (
            str(mailbox.last_failure_reason_code or "")[:64]
            if mailbox is not None
            else ""
        ),
        "last_failure_error_type": (
            str(mailbox.last_failure_error_type or "")[:96]
            if mailbox is not None
            else ""
        ),
    }


def _analysis_mailbox_snapshot(
    session_id: str,
    *,
    default_retry_after_ms: int = 0,
) -> tuple[dict[str, object], dict[str, Any]]:
    normalized_session_id = str(session_id or "").strip()
    with _ANALYSIS_MAILBOX_LOCK:
        mailbox = _ANALYSIS_MAILBOXES.get(normalized_session_id)
        if mailbox is not None:
            mailbox.last_touched_monotonic = time.monotonic()
        public = _public_analysis_mailbox_locked(
            mailbox,
            default_retry_after_ms=default_retry_after_ms,
        )
        last_result = dict(mailbox.last_result or {}) if mailbox is not None else {}
    return public, last_result


def _analysis_mailbox_totals() -> dict[str, object]:
    with _ANALYSIS_MAILBOX_LOCK:
        active_count = sum(
            1
            for mailbox in _ANALYSIS_MAILBOXES.values()
            if not mailbox.retired and mailbox.active_job is not None
        )
        pending_count = sum(
            1
            for mailbox in _ANALYSIS_MAILBOXES.values()
            if not mailbox.retired and mailbox.pending_job is not None
        )
        failed_count = sum(
            1
            for mailbox in _ANALYSIS_MAILBOXES.values()
            if not mailbox.retired and mailbox.last_failed_epoch_ms > 0
        )
        latest_failure_epoch_ms = max(
            (
                mailbox.last_failed_epoch_ms
                for mailbox in _ANALYSIS_MAILBOXES.values()
                if not mailbox.retired
            ),
            default=0,
        )
        state_count = len(_ANALYSIS_MAILBOXES)
    return {
        "schema_version": "PG_FRAME_ANALYSIS_MAILBOX_CAPACITY_V1",
        "analysis_busy": active_count > 0,
        "active_analysis_count": active_count,
        "pending_analysis_count": pending_count,
        "max_active_per_session": 1,
        "max_pending_per_session": 1,
        "latest_pending_replaces": True,
        "mailbox_state_count": state_count,
        "max_mailbox_states": _env_int(
            "PHOENIXGUARD_FRAME_INGEST_MAX_MAILBOX_STATES",
            DEFAULT_MAX_ANALYSIS_MAILBOX_STATES,
            1,
        ),
        "mailboxes_with_failure": failed_count,
        "latest_failure_epoch_ms": max(0, int(latest_failure_epoch_ms)),
    }


def _invalidate_analysis_mailbox_for_session(session_id: str) -> bool:
    """Fence queued work whenever source ownership changes or is killed."""

    normalized_session_id = str(session_id or "").strip()
    with _ANALYSIS_MAILBOX_LOCK:
        mailbox = _ANALYSIS_MAILBOXES.get(normalized_session_id)
        if mailbox is None or mailbox.retired:
            return False
        mailbox.epoch += 1
        mailbox.identity_key = ()
        mailbox.last_result = None
        mailbox.last_completed_frame_id = None
        mailbox.last_completed_epoch_ms = 0
        mailbox.last_failed_frame_id = None
        mailbox.last_failed_epoch_ms = 0
        mailbox.last_failure_reason_code = ""
        mailbox.last_failure_error_type = ""
        mailbox.last_touched_monotonic = time.monotonic()
        pending = mailbox.pending_job
        mailbox.pending_job = None
        if pending is not None:
            pending.image.close()
            return True
    return False


def _analysis_job_still_current(
    mailbox: FrameAnalysisMailbox,
    job: FrameAnalysisJob,
) -> bool:
    with _ANALYSIS_MAILBOX_LOCK:
        return bool(
            not mailbox.retired
            and mailbox.active_job is job
            and job.mailbox_epoch == mailbox.epoch
            and job.identity_key == mailbox.identity_key
        )


def _sanitized_reason_code(value: object, fallback: str) -> str:
    text = "".join(
        character
        for character in str(value or "").upper()
        if character.isalnum() or character in {"_", "-"}
    )[:64]
    return text or fallback


def _audit_async_analysis_event(
    event: str,
    job: FrameAnalysisJob,
    **fields: object,
) -> None:
    allowed_base_fields = {
        key: job.audit_fields[key]
        for key in (
            "session_id",
            "source_id",
            "sequence_id",
            "symbol",
            "timeframe",
            "frame_id",
            "capture_epoch_ms",
            "source_generation",
            "frame_sha256",
            "frame_bytes",
        )
        if key in job.audit_fields
    }
    _security_audit(event, job.audit_context, {**allowed_base_fields, **fields})


def _analysis_job_lease_outcome(job: FrameAnalysisJob) -> FrameAnalysisOutcome:
    # This queue-side check avoids starting known-stale work. The tracker also
    # re-proves this exact lease under its publication lock before every state
    # commit, covering a source replacement that races active model work.
    coordinate_space = str(job.metadata.get("coordinate_space", "") or "").strip()
    if coordinate_space not in LEASED_COORDINATE_SPACES:
        return FrameAnalysisOutcome(state="allowed")
    try:
        validation = job.tracker.validate_external_source_lease(
            job.session_id,
            source_id=job.source_id,
            sequence_id=job.sequence_id,
            source_generation=job.source_generation,
            source_lease_id=job.source_lease_id,
        )
    except Exception as exc:
        _audit_async_analysis_event(
            "frame_analysis_failed",
            job,
            error_type=type(exc).__name__,
            reason_code="LEASE_REVALIDATION_FAILED",
        )
        return FrameAnalysisOutcome(
            state="failed",
            reason_code="LEASE_REVALIDATION_FAILED",
            error_type=type(exc).__name__[:96],
        )
    if bool(validation.get("allowed", False)):
        return FrameAnalysisOutcome(state="allowed")
    _audit_async_analysis_event(
        "frame_analysis_discarded",
        job,
        status_code=int(validation.get("status_code", status.HTTP_409_CONFLICT) or status.HTTP_409_CONFLICT),
        reason_code=_sanitized_reason_code(
            validation.get("reason_code"),
            "SOURCE_SUPERSEDED",
        ),
    )
    return FrameAnalysisOutcome(
        state="discarded",
        reason_code=_sanitized_reason_code(
            validation.get("reason_code"),
            "SOURCE_SUPERSEDED",
        ),
    )


def _run_analysis_job(
    mailbox: FrameAnalysisMailbox,
    job: FrameAnalysisJob,
) -> FrameAnalysisOutcome:
    if not _analysis_job_still_current(mailbox, job):
        _audit_async_analysis_event(
            "frame_analysis_discarded",
            job,
            reason_code="MAILBOX_SUPERSEDED",
        )
        return FrameAnalysisOutcome(
            state="discarded",
            reason_code="MAILBOX_SUPERSEDED",
        )
    lease_outcome = _analysis_job_lease_outcome(job)
    if lease_outcome.state != "allowed":
        return lease_outcome
    if not _analysis_job_still_current(mailbox, job):
        _audit_async_analysis_event(
            "frame_analysis_discarded",
            job,
            reason_code="MAILBOX_SUPERSEDED",
        )
        return FrameAnalysisOutcome(
            state="discarded",
            reason_code="MAILBOX_SUPERSEDED",
        )
    try:
        tracker_response = job.tracker.ingest_external_frame(
            job.session_id,
            job.image,
            source_id=job.source_id,
            symbol=job.symbol,
            timeframe=job.timeframe,
            source_url=job.source_url,
            sequence_id=job.sequence_id,
            capture_epoch_ms=job.capture_epoch_ms,
            frame_id=job.frame_id,
            metadata=job.metadata,
        )
        frame_ingest_value = tracker_response.get("frame_ingest", {})
        frame_ingest_result = (
            cast(Mapping[str, Any], frame_ingest_value)
            if isinstance(frame_ingest_value, Mapping)
            else cast(Mapping[str, Any], {})
        )
        if frame_ingest_result.get("accepted") is False:
            failure_reason = _sanitized_reason_code(
                frame_ingest_result.get("failure_reason_code"),
                "FRAME_STUDY_NOT_ACCEPTED",
            )
            failure_error_type = "".join(
                character
                for character in str(
                    frame_ingest_result.get("failure_error_type", "") or ""
                )
                if character.isalnum() or character in {"_", ".", "-"}
            )[:96]
            _audit_async_analysis_event(
                "frame_analysis_failed",
                job,
                status_code=0,
                reason_code=failure_reason,
                error_type=failure_error_type,
            )
            return FrameAnalysisOutcome(
                state="failed",
                reason_code=failure_reason,
                error_type=failure_error_type,
            )
        bounded_result = _bounded_analysis_result(tracker_response)
    except Exception as exc:
        lease_status = int(getattr(exc, "status_code", 0) or 0)
        lease_reason = _sanitized_reason_code(
            getattr(exc, "reason_code", ""),
            "FRAME_ANALYSIS_FAILED",
        )
        discarded = lease_status in {
            status.HTTP_409_CONFLICT,
            status.HTTP_410_GONE,
        }
        error_type = "".join(
            character
            for character in str(
                getattr(exc, "error_type", type(exc).__name__)
                or type(exc).__name__
            )
            if character.isalnum() or character in {"_", ".", "-"}
        )[:96]
        if not discarded:
            fail_transition = getattr(
                job.tracker,
                "fail_external_frame_analysis",
                None,
            )
            if callable(fail_transition):
                try:
                    fail_transition(
                        job.session_id,
                        source_id=job.source_id,
                        sequence_id=job.sequence_id,
                        source_generation=job.source_generation,
                        source_lease_id=job.source_lease_id,
                        source_type=str(
                            job.metadata.get("source_type", "") or ""
                        ).strip(),
                        coordinate_space=str(
                            job.metadata.get("coordinate_space", "") or ""
                        ).strip(),
                        capture_epoch_ms=job.capture_epoch_ms,
                        frame_id=job.frame_id,
                        reason_code=lease_reason,
                        error_type=error_type,
                    )
                except Exception as cleanup_exc:
                    _audit_async_analysis_event(
                        "frame_analysis_failure_transition_failed",
                        job,
                        reason_code="FAILURE_TRANSITION_FAILED",
                        error_type=type(cleanup_exc).__name__,
                    )
        _audit_async_analysis_event(
            "frame_analysis_discarded"
            if discarded
            else "frame_analysis_failed",
            job,
            status_code=lease_status,
            reason_code=lease_reason,
            error_type=error_type,
        )
        return FrameAnalysisOutcome(
            state="discarded" if discarded else "failed",
            reason_code=lease_reason,
            error_type=error_type,
        )
    _audit_async_analysis_event("frame_analysis_completed", job)
    return FrameAnalysisOutcome(state="completed", result=bounded_result)


def _analysis_mailbox_worker(mailbox: FrameAnalysisMailbox) -> None:
    while True:
        with _ANALYSIS_MAILBOX_LOCK:
            if mailbox.retired:
                active = mailbox.active_job
                mailbox.active_job = None
                mailbox.worker = None
                if active is not None:
                    active.image.close()
                return
            job = mailbox.active_job
        if job is None:
            with _ANALYSIS_MAILBOX_LOCK:
                mailbox.worker = None
            return
        outcome = FrameAnalysisOutcome(state="discarded")
        transient_retry_count = 0
        try:
            while True:
                outcome = _run_analysis_job(mailbox, job)
                retry_limit = _env_int(
                    "PHOENIXGUARD_FRAME_STUDY_TRANSIENT_RETRY_LIMIT",
                    2,
                    0,
                )
                if not (
                    outcome.state == "failed"
                    and outcome.reason_code == "FRAME_STUDY_NOT_ACCEPTED"
                    and transient_retry_count < retry_limit
                ):
                    break
                with _ANALYSIS_MAILBOX_LOCK:
                    retry_is_current = bool(
                        not mailbox.retired
                        and mailbox.active_job is job
                        and mailbox.pending_job is None
                        and job.mailbox_epoch == mailbox.epoch
                        and job.identity_key == mailbox.identity_key
                    )
                if not retry_is_current:
                    break
                base_delay_ms = _env_int(
                    "PHOENIXGUARD_FRAME_STUDY_TRANSIENT_RETRY_DELAY_MS",
                    750,
                    1,
                )
                retry_delay_ms = min(
                    5_000,
                    base_delay_ms * (2**transient_retry_count),
                )
                transient_retry_count += 1
                _audit_async_analysis_event(
                    "frame_analysis_retry_scheduled",
                    job,
                    reason_code=outcome.reason_code,
                    status_code=0,
                )
                time.sleep(retry_delay_ms / 1000.0)
                with _ANALYSIS_MAILBOX_LOCK:
                    retry_is_current = bool(
                        not mailbox.retired
                        and mailbox.active_job is job
                        and mailbox.pending_job is None
                        and job.mailbox_epoch == mailbox.epoch
                        and job.identity_key == mailbox.identity_key
                    )
                if not retry_is_current:
                    break
        finally:
            job.image.close()
        with _ANALYSIS_MAILBOX_LOCK:
            if mailbox.active_job is job:
                mailbox.active_job = None
            if (
                outcome.state in {"completed", "failed"}
                and not mailbox.retired
                and job.mailbox_epoch == mailbox.epoch
                and job.identity_key == mailbox.identity_key
            ):
                outcome_epoch_ms = int(round(time.time() * 1000.0))
                mailbox.last_touched_monotonic = time.monotonic()
                if outcome.state == "completed":
                    mailbox.last_result = dict(outcome.result or {})
                    mailbox.last_completed_frame_id = int(job.frame_id)
                    mailbox.last_completed_epoch_ms = outcome_epoch_ms
                else:
                    mailbox.last_failed_frame_id = int(job.frame_id)
                    mailbox.last_failed_epoch_ms = outcome_epoch_ms
                    mailbox.last_failure_reason_code = _sanitized_reason_code(
                        outcome.reason_code,
                        "FRAME_ANALYSIS_FAILED",
                    )
                    mailbox.last_failure_error_type = str(
                        outcome.error_type or ""
                    )[:96]
            if mailbox.retired:
                pending = mailbox.pending_job
                mailbox.pending_job = None
                mailbox.worker = None
                if pending is not None:
                    pending.image.close()
                return
            if mailbox.pending_job is None:
                mailbox.worker = None
                return
            mailbox.active_job = mailbox.pending_job
            mailbox.pending_job = None


def _start_analysis_worker(worker: threading.Thread) -> None:
    worker.start()


def _prune_idle_analysis_mailboxes_locked(*, reserve_for_session_id: str) -> None:
    """Keep the per-session mailbox registry bounded across session churn."""

    maximum = _env_int(
        "PHOENIXGUARD_FRAME_INGEST_MAX_MAILBOX_STATES",
        DEFAULT_MAX_ANALYSIS_MAILBOX_STATES,
        1,
    )
    if reserve_for_session_id in _ANALYSIS_MAILBOXES:
        return
    while len(_ANALYSIS_MAILBOXES) >= maximum:
        idle = [
            mailbox
            for mailbox in _ANALYSIS_MAILBOXES.values()
            if mailbox.active_job is None and mailbox.pending_job is None
        ]
        if not idle:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "reason_code": "ANALYSIS_CAPACITY_REACHED",
                    "message": "All bounded frame-analysis mailboxes are currently active.",
                },
            )
        oldest = min(idle, key=lambda item: float(item.last_touched_monotonic or 0.0))
        oldest.retired = True
        oldest.epoch += 1
        _ANALYSIS_MAILBOXES.pop(oldest.session_id, None)


def _enqueue_analysis_job(
    job: FrameAnalysisJob,
    *,
    retry_after_ms: int,
) -> tuple[dict[str, object], str, dict[str, Any]]:
    worker_to_start: threading.Thread | None = None
    replaced = False
    with _ANALYSIS_MAILBOX_LOCK:
        mailbox = _ANALYSIS_MAILBOXES.get(job.session_id)
        if mailbox is None or mailbox.retired:
            _prune_idle_analysis_mailboxes_locked(
                reserve_for_session_id=job.session_id,
            )
            mailbox = FrameAnalysisMailbox(
                session_id=job.session_id,
                last_touched_monotonic=time.monotonic(),
            )
            _ANALYSIS_MAILBOXES[job.session_id] = mailbox
        if mailbox.identity_key and mailbox.identity_key != job.identity_key:
            mailbox.epoch += 1
            mailbox.last_result = None
            old_pending = mailbox.pending_job
            mailbox.pending_job = None
            if old_pending is not None:
                old_pending.image.close()
        mailbox.identity_key = job.identity_key
        mailbox.last_touched_monotonic = time.monotonic()
        mailbox.retry_after_ms = min(300_000, max(0, int(retry_after_ms or 0)))
        job.mailbox_epoch = mailbox.epoch
        if mailbox.active_job is None:
            mailbox.active_job = job
            worker_to_start = threading.Thread(
                target=_analysis_mailbox_worker,
                args=(mailbox,),
                name=f"phoenixguard-frame-analysis-{job.session_id[:48]}",
                daemon=True,
            )
            mailbox.worker = worker_to_start
            disposition = "active"
        else:
            if mailbox.pending_job is not None:
                mailbox.pending_job.image.close()
                mailbox.replaced_frame_count = min(
                    2_147_483_647,
                    mailbox.replaced_frame_count + 1,
                )
                replaced = True
            mailbox.pending_job = job
            disposition = "replaced_pending" if replaced else "pending"
        public = _public_analysis_mailbox_locked(mailbox)
        last_result = dict(mailbox.last_result or {})
    if worker_to_start is not None:
        try:
            _start_analysis_worker(worker_to_start)
        except Exception as exc:
            orphaned_pending: FrameAnalysisJob | None = None
            with _ANALYSIS_MAILBOX_LOCK:
                if mailbox.active_job is job:
                    mailbox.active_job = None
                if mailbox.worker is worker_to_start:
                    mailbox.worker = None
                orphaned_pending = mailbox.pending_job
                mailbox.pending_job = None
                mailbox.epoch += 1
                mailbox.identity_key = ()
                mailbox.last_result = None
                failed_job = orphaned_pending or job
                mailbox.last_failed_frame_id = int(failed_job.frame_id)
                mailbox.last_failed_epoch_ms = int(round(time.time() * 1000.0))
                mailbox.last_failure_reason_code = "WORKER_START_FAILED"
                mailbox.last_failure_error_type = type(exc).__name__[:96]
                mailbox.last_touched_monotonic = time.monotonic()
            job.image.close()
            _audit_async_analysis_event(
                "frame_analysis_failed",
                job,
                error_type=type(exc).__name__,
                reason_code="WORKER_START_FAILED",
            )
            if orphaned_pending is not None:
                orphaned_pending.image.close()
                _audit_async_analysis_event(
                    "frame_analysis_failed",
                    orphaned_pending,
                    error_type=type(exc).__name__,
                    reason_code="WORKER_START_FAILED",
                )
            raise
    return public, disposition, last_result


def _signature_required(context: FeedAuthContext) -> bool:
    if _env_bool("PHOENIXGUARD_FRAME_INGEST_REQUIRE_SIGNATURE", False):
        return True
    if context.signing_secret:
        return _env_bool("PHOENIXGUARD_FRAME_INGEST_REQUIRE_SIGNATURE_WHEN_SECRET_SET", True)
    return False


def _canonical_frame_signature_payload(
    *,
    method: str,
    path: str,
    session_id: str,
    source_id: str,
    sequence_id: str,
    frame_id: int,
    capture_epoch_ms: int,
    frame_sha256: str,
    timestamp: str,
    nonce: str,
) -> str:
    return "\n".join(
        [
            "PG_FRAME_INGEST_V1",
            method.upper(),
            path,
            str(session_id or "").strip(),
            str(source_id or "").strip(),
            str(sequence_id or "").strip(),
            str(int(frame_id or 0)),
            str(int(capture_epoch_ms or 0)),
            str(frame_sha256 or "").strip().lower(),
            str(timestamp or "").strip(),
            str(nonce or "").strip(),
        ]
    )


def _normalize_signature(raw_signature: str) -> str:
    text = str(raw_signature or "").strip()
    if text.startswith("v1="):
        text = text[3:]
    return text.lower()


def _prune_signature_nonces(now_monotonic: float) -> None:
    expired = [key for key, expiry in _SIGNATURE_NONCES.items() if expiry <= now_monotonic]
    for key in expired:
        _SIGNATURE_NONCES.pop(key, None)


def _record_or_reject_signature_nonce(context: FeedAuthContext, nonce: str) -> None:
    ttl_sec = _env_int("PHOENIXGUARD_FRAME_INGEST_SIGNATURE_NONCE_TTL_SEC", DEFAULT_SIGNATURE_NONCE_TTL_SEC, 60)
    now_monotonic = time.monotonic()
    nonce_key = f"{context.token_name}:{context.user_id}:{nonce}"
    with _SIGNATURE_NONCE_LOCK:
        _prune_signature_nonces(now_monotonic)
        if nonce_key in _SIGNATURE_NONCES:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Frame ingest signature nonce was already used.")
        _SIGNATURE_NONCES[nonce_key] = now_monotonic + float(ttl_sec)


def _require_frame_signature(
    context: FeedAuthContext,
    request: Request,
    *,
    session_id: str,
    source_id: str,
    sequence_id: str,
    capture_epoch_ms: int,
    frame_id: int,
    frame_sha256: str,
) -> None:
    if not _signature_required(context):
        return
    if not context.signing_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Frame ingest signature is required but no signing secret is configured.")
    timestamp = str(request.headers.get("x-phoenixguard-timestamp") or "").strip()
    nonce = str(request.headers.get("x-phoenixguard-nonce") or "").strip()
    supplied_signature = _normalize_signature(str(request.headers.get("x-phoenixguard-signature") or ""))
    if not timestamp or not nonce or not supplied_signature:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Frame ingest signature headers are required.")
    try:
        timestamp_ms = int(float(timestamp))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Frame ingest signature timestamp is invalid.") from exc
    now_ms = int(round(time.time() * 1000.0))
    max_skew_ms = _env_int("PHOENIXGUARD_FRAME_INGEST_SIGNATURE_MAX_SKEW_SEC", DEFAULT_SIGNATURE_MAX_SKEW_SEC, 30) * 1000
    if abs(now_ms - timestamp_ms) > max_skew_ms:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Frame ingest signature timestamp is outside the allowed skew.")
    canonical = _canonical_frame_signature_payload(
        method=request.method,
        path=request.url.path,
        session_id=session_id,
        source_id=source_id,
        sequence_id=sequence_id,
        capture_epoch_ms=int(capture_epoch_ms or 0),
        frame_id=int(frame_id or 0),
        frame_sha256=frame_sha256,
        timestamp=timestamp,
        nonce=nonce,
    )
    expected_signature = hmac.new(context.signing_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Frame ingest signature is invalid.")
    _record_or_reject_signature_nonce(context, nonce)


def _require_scope_allowed(context: FeedAuthContext, session_id: str, source_id: str, symbol: str, timeframe: str) -> None:
    if context.global_token:
        return
    normalized_session = str(session_id or "").strip()
    normalized_source = str(source_id or "").strip()
    normalized_symbol = str(symbol or "").strip().upper()
    normalized_timeframe = str(timeframe or "").strip().upper()
    if context.allowed_session_prefixes and not any(normalized_session.startswith(prefix) for prefix in context.allowed_session_prefixes):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Frame ingest token is not allowed to feed this session.",
        )
    if context.allowed_source_ids and normalized_source not in context.allowed_source_ids:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Frame ingest token is not allowed to use this source_id.")
    if context.allowed_symbols and normalized_symbol and normalized_symbol not in context.allowed_symbols:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Frame ingest token is not allowed to use this symbol.")
    if context.allowed_timeframes and normalized_timeframe and normalized_timeframe not in context.allowed_timeframes:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Frame ingest token is not allowed to use this timeframe.")


def _require_session_scope_allowed(context: FeedAuthContext, session_id: str) -> None:
    if context.global_token:
        return
    normalized_session = str(session_id or "").strip()
    if context.allowed_session_prefixes and not any(normalized_session.startswith(prefix) for prefix in context.allowed_session_prefixes):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Frame ingest token is not allowed to read this session.")


def _require_origin_allowed(origin: str | None) -> None:
    allowed_origins = _env_csv("PHOENIXGUARD_FRAME_INGEST_ALLOWED_ORIGINS")
    if not allowed_origins or not origin:
        return
    if str(origin).strip() not in allowed_origins:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Frame ingest origin is not allowed.")


def _metadata_from_json(raw_json: str) -> dict[str, Any]:
    text = str(raw_json or "").strip()
    if not text:
        return {}
    max_metadata_bytes = _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_METADATA_BYTES", DEFAULT_MAX_METADATA_BYTES, 512)
    if len(text.encode("utf-8")) > max_metadata_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="metadata_json is too large.")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata_json is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata_json must be a JSON object.")
    parsed_mapping = cast(Mapping[str, Any], parsed)
    metadata = {str(key): value for key, value in parsed_mapping.items()}
    source_type = str(metadata.get("source_type", "") or "").strip()
    if source_type and source_type not in SUPPORTED_FRAME_SOURCE_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata_json source_type is not supported.")
    coordinate_space = str(metadata.get("coordinate_space", "") or "").strip()
    if source_type in {"browser_extension_capture", "browser_tab_roi_capture"} and coordinate_space not in BROWSER_EXTENSION_COORDINATE_SPACES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Browser extension frames must declare coordinate_space as one of: "
                + ", ".join(BROWSER_EXTENSION_COORDINATE_SPACES)
                + "."
            ),
        )
    if source_type == "windows_graphics_capture_roi" and coordinate_space != "wgc_hwnd_roi_v1":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Windows Graphics Capture ROI frames must declare coordinate_space=wgc_hwnd_roi_v1.",
        )
    for field_name in _SERVER_METADATA_FIELDS:
        metadata.pop(field_name, None)
    return metadata


def _feed_key(context: FeedAuthContext, session_id: str, source_id: str) -> str:
    token_scope = context.token_name if context.global_token else f"{context.token_name}:{context.user_id}"
    return "|".join(
        [
            token_scope,
            str(session_id or "").strip(),
            str(source_id or "").strip(),
        ]
    )


def _active_feed_states(now_monotonic: float) -> list[FeedRuntimeState]:
    active_window = _env_int("PHOENIXGUARD_FRAME_INGEST_ACTIVE_FEED_WINDOW_SEC", DEFAULT_ACTIVE_FEED_WINDOW_SEC, 30)
    expired_keys = [
        key for key, value in _FEED_RUNTIME_STATE.items() if now_monotonic - float(value.last_seen_monotonic) > active_window
    ]
    for key in expired_keys:
        _FEED_RUNTIME_STATE.pop(key, None)
    return list(_FEED_RUNTIME_STATE.values())


def _retire_feed_runtime_for_session(session_id: str) -> int:
    """Release capacity held by every superseded transport for one session."""

    normalized_session_id = str(session_id or "").strip()
    with _FEED_STATE_LOCK:
        matching_keys = [
            key
            for key, state in _FEED_RUNTIME_STATE.items()
            if state.session_id == normalized_session_id
        ]
        for key in matching_keys:
            _FEED_RUNTIME_STATE.pop(key, None)
    return len(matching_keys)


def _reserve_feed_runtime(
    context: FeedAuthContext,
    *,
    session_id: str,
    source_id: str,
    sequence_id: str,
    source_generation: int,
    capture_epoch_ms: int,
    frame_id: int,
) -> FeedRuntimeReservation:
    if _env_bool("PHOENIXGUARD_FRAME_INGEST_REQUIRE_CAPTURE_EPOCH", True) and int(capture_epoch_ms or 0) <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="capture_epoch_ms is required for live frame ingest.")
    if _env_bool("PHOENIXGUARD_FRAME_INGEST_REQUIRE_FRAME_ID", True) and int(frame_id or 0) <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="frame_id is required for live frame ingest.")
    key = _feed_key(context, session_id, source_id)
    now_monotonic = time.monotonic()
    with _FEED_STATE_LOCK:
        normalized_session_id = str(session_id or "").strip()
        normalized_generation = max(0, int(source_generation or 0))
        _active_feed_states(now_monotonic)
        superseded_keys: list[str] = []
        if normalized_generation > 0:
            superseded_keys = [
                state_key
                for state_key, state in _FEED_RUNTIME_STATE.items()
                if state.session_id == normalized_session_id
                and int(state.source_generation or 0) != normalized_generation
            ]
        superseded_key_set = set(superseded_keys)
        retired_states = [
            (state_key, _FEED_RUNTIME_STATE[state_key])
            for state_key in superseded_keys
            if state_key in _FEED_RUNTIME_STATE
        ]
        active_states = [
            state
            for state_key, state in _FEED_RUNTIME_STATE.items()
            if state_key not in superseded_key_set
        ]
        existing = None if key in superseded_key_set else _FEED_RUNTIME_STATE.get(key)
        same_sequence = bool(
            existing is not None
            and existing.sequence_id == str(sequence_id or "").strip()
        )
        if existing is not None:
            elapsed = now_monotonic - float(existing.last_seen_monotonic)
            if elapsed < float(context.min_interval_sec):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Frame feed interval is too fast. Wait at least {context.min_interval_sec} seconds between frames.",
                    headers={"Retry-After": str(int(context.min_interval_sec))},
                )
        if existing is not None and same_sequence:
            if int(capture_epoch_ms or 0) <= int(existing.last_capture_epoch_ms or 0):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Frame feed capture_epoch_ms did not advance within this sequence.",
                )
            if int(frame_id or 0) <= int(existing.last_frame_id or 0):
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Frame feed frame_id did not advance within this sequence.",
                )
        token_active = [
            state
            for state in active_states
            if state.token_name == context.token_name and state.user_id == context.user_id
        ]
        if existing is None and len(token_active) >= int(context.max_active_feeds):
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Frame ingest token has reached its active feed limit.")
        total_limit = _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_ACTIVE_FEEDS_TOTAL", DEFAULT_MAX_ACTIVE_FEEDS_TOTAL, 1)
        if existing is None and len(active_states) >= total_limit:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="PhoenixGuard active frame feed capacity is full.")
        for state_key in superseded_keys:
            _FEED_RUNTIME_STATE.pop(state_key, None)
        current = FeedRuntimeState(
            token_name=context.token_name,
            user_id=context.user_id,
            session_id=normalized_session_id,
            source_id=str(source_id or "").strip(),
            sequence_id=str(sequence_id or "").strip(),
            source_generation=normalized_generation,
            last_capture_epoch_ms=int(capture_epoch_ms or 0),
            last_frame_id=int(frame_id or 0),
            last_seen_monotonic=now_monotonic,
        )
        _FEED_RUNTIME_STATE[key] = current
        return FeedRuntimeReservation(
            key=key,
            current=current,
            previous=existing,
            retired_states=tuple(retired_states),
        )


def _rollback_feed_runtime_reservation(reservation: FeedRuntimeReservation | None) -> None:
    if reservation is None:
        return
    with _FEED_STATE_LOCK:
        if _FEED_RUNTIME_STATE.get(reservation.key) is not reservation.current:
            return
        _FEED_RUNTIME_STATE.pop(reservation.key, None)
        if reservation.previous is not None:
            _FEED_RUNTIME_STATE[reservation.key] = reservation.previous
        for state_key, state in reservation.retired_states:
            _FEED_RUNTIME_STATE.setdefault(state_key, state)


def _readiness_payload() -> dict[str, object]:
    registry_path = _token_registry_path()
    registry_configured = registry_path is not None
    global_token_configured = bool(_configured_ingest_token())
    registry_valid = False
    registry_error = ""
    if registry_path is not None:
        try:
            registry_valid = bool(_load_token_registry())
        except HTTPException as exc:
            registry_error = str(exc.detail)
    armed = global_token_configured or registry_valid
    with _FEED_STATE_LOCK:
        active_count = len(_active_feed_states(time.monotonic()))
    return {
        "schema_version": "PG_FRAME_INGEST_READINESS_V1",
        "status": "ready" if armed else "not_armed",
        "armed": armed,
        "global_token_configured": global_token_configured,
        "token_registry_configured": registry_configured,
        "token_registry_valid": registry_valid,
        "token_registry_error": registry_error,
        "active_feed_count": active_count,
        "max_active_feeds_total": _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_ACTIVE_FEEDS_TOTAL", DEFAULT_MAX_ACTIVE_FEEDS_TOTAL, 1),
        "max_active_feeds_per_token": _env_int(
            "PHOENIXGUARD_FRAME_INGEST_MAX_ACTIVE_FEEDS_PER_TOKEN",
            DEFAULT_MAX_ACTIVE_FEEDS_PER_TOKEN,
            1,
        ),
        "min_interval_sec": _env_int("PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC", DEFAULT_MIN_INTERVAL_SEC, 1),
        "max_source_age_sec": _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_SOURCE_AGE_SEC", 180, 5),
        "require_capture_epoch": _env_bool("PHOENIXGUARD_FRAME_INGEST_REQUIRE_CAPTURE_EPOCH", True),
        "require_frame_id": _env_bool("PHOENIXGUARD_FRAME_INGEST_REQUIRE_FRAME_ID", True),
        "signature_required": _env_bool("PHOENIXGUARD_FRAME_INGEST_REQUIRE_SIGNATURE", False),
        "signature_nonce_ttl_sec": _env_int(
            "PHOENIXGUARD_FRAME_INGEST_SIGNATURE_NONCE_TTL_SEC",
            DEFAULT_SIGNATURE_NONCE_TTL_SEC,
            60,
        ),
        "security_audit_log_enabled": _env_bool("PHOENIXGUARD_SECURITY_AUDIT_LOG_ENABLED", True),
        "security_audit_log_path": str(_security_audit_log_path()),
        "allowed_origins_configured": bool(_env_csv("PHOENIXGUARD_FRAME_INGEST_ALLOWED_ORIGINS")),
        "analysis_mailbox": _analysis_mailbox_totals(),
    }


async def _read_image_upload(frame: UploadFile) -> tuple[Image.Image, str, int]:
    data = await frame.read()
    max_bytes = _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_BYTES", DEFAULT_MAX_FRAME_BYTES, 1024 * 1024)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Frame upload is empty.")
    if len(data) > max_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Frame upload is too large.")
    max_pixels = _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_PIXELS", DEFAULT_MAX_FRAME_PIXELS, 1024 * 1024)
    Image.MAX_IMAGE_PIXELS = max_pixels
    allowed_formats = {item.upper() for item in (_env_csv("PHOENIXGUARD_FRAME_INGEST_ALLOWED_FORMATS") or ("PNG", "JPEG", "WEBP"))}
    try:
        with Image.open(BytesIO(data)) as probe:
            detected_format = str(probe.format or "").upper()
            if detected_format not in allowed_formats:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Frame upload format is not allowed.")
            if getattr(probe, "is_animated", False):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Animated frame uploads are not allowed.")
            probe.verify()
        with Image.open(BytesIO(data)) as reopened:
            image = reopened.convert("RGB")
    except HTTPException:
        raise
    except Image.DecompressionBombError as exc:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Frame upload exceeds the configured pixel safety limit.") from exc
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Frame upload is not a readable image.") from exc
    min_side = _env_int("PHOENIXGUARD_FRAME_INGEST_MIN_SIDE", DEFAULT_MIN_FRAME_SIDE, 16)
    max_side = _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_SIDE", DEFAULT_MAX_FRAME_SIDE, 512)
    if image.width < min_side or image.height < min_side:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Frame is too small to be a chart.")
    if image.width > max_side or image.height > max_side:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Frame is larger than the configured ingest limit.")
    return image, hashlib.sha256(data).hexdigest(), len(data)


def build_frame_ingest_router(get_tracker: Callable[[], FrameIngestTracker]) -> APIRouter:
    router = APIRouter(prefix="/v1/mobile/frame-ingest", tags=["frame-ingest"])

    def _public_source_control(value: object, *, include_lease: bool = False) -> dict[str, object]:
        if not isinstance(value, Mapping):
            return {}
        source = dict(cast(Mapping[str, Any], value))
        if not include_lease:
            source.pop("source_lease_id", None)
            source.pop("lease_id", None)
        source.pop("target_private", None)
        return cast(dict[str, object], source)

    def _source_lease_rejection(status_code: int, reason_code: str, message: str) -> HTTPException:
        return HTTPException(
            status_code=int(status_code),
            detail={"reason_code": str(reason_code), "message": str(message)},
        )

    def _require_current_frame_source_contract(
        session_id: str,
        *,
        source_id: str,
        sequence_id: str,
        source_generation: int,
        source_lease_id: str,
        metadata: Mapping[str, Any],
    ) -> None:
        tracker = get_tracker()
        incoming_source_type = str(metadata.get("source_type", "") or "").strip()
        incoming_coordinate_space = str(metadata.get("coordinate_space", "") or "").strip()
        if incoming_coordinate_space in LEASED_COORDINATE_SPACES:
            expected_source_type = {
                "edge_tab_roi_v1": "browser_tab_roi_capture",
                "wgc_hwnd_roi_v1": "windows_graphics_capture_roi",
            }.get(incoming_coordinate_space, "")
            if (
                incoming_source_type != expected_source_type
                or not str(source_id or "").strip()
                or not str(sequence_id or "").strip()
                or int(source_generation or 0) <= 0
                or not str(source_lease_id or "").strip()
            ):
                raise _source_lease_rejection(
                    status.HTTP_409_CONFLICT,
                    "SOURCE_SUPERSEDED",
                    "This frame does not match the currently claimed chart source contract.",
                )
            try:
                validation = tracker.validate_external_source_lease(
                    session_id,
                    source_id=source_id,
                    sequence_id=sequence_id,
                    source_generation=int(source_generation or 0),
                    source_lease_id=str(source_lease_id or ""),
                )
            except KeyError as exc:
                raise _source_lease_rejection(
                    status.HTTP_409_CONFLICT,
                    "SOURCE_CLAIM_REQUIRED",
                    "A current chart source claim is required before leased frames can be uploaded.",
                ) from exc
            if not bool(validation.get("allowed", False)):
                raise _source_lease_rejection(
                    int(validation.get("status_code", status.HTTP_409_CONFLICT) or status.HTTP_409_CONFLICT),
                    str(validation.get("reason_code", "SOURCE_SUPERSEDED") or "SOURCE_SUPERSEDED"),
                    str(validation.get("message", "Source lease is not current.") or "Source lease is not current."),
                )
            return

        # Legacy/unleased sources remain supported, but they may not bypass a
        # currently claimed leased chart. This cold path can read the full
        # snapshot because the background extension uses the fast leased path.
        try:
            snapshot = tracker.get_session_snapshot(session_id)
        except KeyError:
            return
        capture_source_value: object = snapshot.get("capture_source_v3", {})
        capture_source = (
            dict(cast(Mapping[str, Any], capture_source_value))
            if isinstance(capture_source_value, Mapping)
            else {}
        )
        claimed_source_exists = bool(
            str(capture_source.get("source_id", "") or "").strip()
            and int(capture_source.get("source_generation", 0) or 0) > 0
            and str(capture_source.get("coordinate_space", "") or "").strip()
            in LEASED_COORDINATE_SPACES
        )
        if claimed_source_exists:
            state = str(capture_source.get("state", "NO_SOURCE") or "NO_SOURCE").strip().upper()
            raise _source_lease_rejection(
                status.HTTP_410_GONE if state == "KILLED" else status.HTTP_409_CONFLICT,
                "SOURCE_KILLED" if state == "KILLED" else "SOURCE_SUPERSEDED",
                "A claimed chart source owns this session; legacy frames cannot replace it.",
            )

    def frame_ingest_config() -> dict[str, object]:
        readiness = _readiness_payload()
        min_interval_sec = _env_int(
            "PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC",
            DEFAULT_MIN_INTERVAL_SEC,
            1,
        )
        return {
            "schema_version": "PG_FRAME_INGEST_CONFIG_V1",
            "token_required": True,
            "scoped_tokens_supported": True,
            "token_registry_configured": _token_registry_path() is not None,
            "readiness": readiness,
            "max_frame_bytes": _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_BYTES", DEFAULT_MAX_FRAME_BYTES, 1024 * 1024),
            "min_frame_side": _env_int("PHOENIXGUARD_FRAME_INGEST_MIN_SIDE", DEFAULT_MIN_FRAME_SIDE, 16),
            "max_frame_side": _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_SIDE", DEFAULT_MAX_FRAME_SIDE, 512),
            "min_interval_sec": min_interval_sec,
            "retry_after_ms": min(300_000, max(1_000, min_interval_sec * 1000)),
            "max_active_feeds_total": readiness["max_active_feeds_total"],
            "max_active_feeds_per_token": readiness["max_active_feeds_per_token"],
            "analysis_mailbox": readiness["analysis_mailbox"],
            "supported_sources": list(SUPPORTED_FRAME_SOURCE_TYPES),
            "browser_extension_coordinate_space": "edge_tab_content_v1",
            "browser_extension_coordinate_spaces": list(BROWSER_EXTENSION_COORDINATE_SPACES),
            "leased_coordinate_spaces": sorted(LEASED_COORDINATE_SPACES),
        }

    def frame_ingest_readiness() -> dict[str, object]:
        payload = _readiness_payload()
        if not bool(payload.get("armed", False)):
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=payload)
        return payload

    def mobile_frame_uploader() -> HTMLResponse:
        try:
            html = _MOBILE_UPLOADER_PATH.read_text(encoding="utf-8")
        except OSError:
            html = "<!doctype html><title>PhoenixGuard frame ingest</title><p>Mobile frame uploader asset is missing.</p>"
        return HTMLResponse(html)

    def claim_source_control(
        request: Request,
        session_id: str,
        payload: dict[str, Any] = Body(...),
        authorization: str | None = Header(default=None),
        x_phoenixguard_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        _require_origin_allowed(request.headers.get("origin"))
        auth_context = _require_ingest_token(authorization, x_phoenixguard_token)
        source_id = str(payload.get("source_id", "") or "").strip()
        sequence_id = str(payload.get("sequence_id", "") or "").strip()
        source_type = str(payload.get("source_type", "") or "").strip()
        selection_id = str(payload.get("selection_id", "") or "").strip()
        display_name = str(payload.get("display_name", "Selected chart") or "Selected chart").strip()
        coordinate_space = str(payload.get("coordinate_space", "") or "").strip()
        expected_source_value = payload.get("expected_source_control")
        if expected_source_value is not None and not isinstance(expected_source_value, Mapping):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="expected_source_control must be a source-control object.",
            )
        expected_source_control = (
            dict(cast(Mapping[str, Any], expected_source_value))
            if isinstance(expected_source_value, Mapping)
            else None
        )
        _require_scope_allowed(auth_context, session_id, source_id, "", "")
        if not source_id or len(source_id) > 128 or not sequence_id or len(sequence_id) > 192:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="source_id and sequence_id are required for a source claim.",
            )
        if source_type not in {"browser_tab_roi_capture", "windows_graphics_capture_roi"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Source claims support browser_tab_roi_capture or windows_graphics_capture_roi.",
            )
        expected_space = "edge_tab_roi_v1" if source_type == "browser_tab_roi_capture" else "wgc_hwnd_roi_v1"
        if coordinate_space != expected_space:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"{source_type} must claim coordinate_space={expected_space}.",
            )
        try:
            state = get_tracker().claim_external_source(
                session_id,
                source_id=source_id,
                sequence_id=sequence_id,
                source_type=source_type,
                selection_id=selection_id,
                display_name=display_name[:180],
                coordinate_space=coordinate_space,
                expected_source_control=expected_source_control,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        except Exception as exc:
            lease_status = int(getattr(exc, "status_code", 0) or 0)
            lease_reason = str(getattr(exc, "reason_code", "") or "")
            if lease_status in {status.HTTP_409_CONFLICT, status.HTTP_410_GONE} and lease_reason:
                detail_factory = getattr(exc, "as_detail", None)
                detail = detail_factory() if callable(detail_factory) else {
                    "reason_code": lease_reason,
                    "message": str(getattr(exc, "message", "") or str(exc)),
                }
                raise HTTPException(status_code=lease_status, detail=detail) from exc
            raise
        _invalidate_analysis_mailbox_for_session(session_id)
        _retire_feed_runtime_for_session(session_id)
        _security_audit(
            "frame_source_claimed",
            auth_context,
            {
                "session_id": session_id,
                "source_id": source_id,
                "sequence_id": sequence_id,
                "source_type": source_type,
                "source_generation": int(state.get("source_generation", 0) or 0),
            },
        )
        return {
            "schema_version": "PG_CAPTURE_SOURCE_CLAIM_ACCEPTED_V1",
            "accepted": True,
            "session_id": session_id,
            "source_control": _public_source_control(state, include_lease=True),
            "source_generation": int(state.get("source_generation", 0) or 0),
            "source_lease_id": str(state.get("source_lease_id", "") or state.get("lease_id", "") or ""),
        }

    async def heartbeat_source_control(
        request: Request,
        session_id: str,
        payload: dict[str, Any] = Body(...),
        authorization: str | None = Header(default=None),
        x_phoenixguard_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        """Acknowledge source transport health without waiting for inference."""

        _require_origin_allowed(request.headers.get("origin"))
        auth_context = _require_ingest_token(authorization, x_phoenixguard_token)
        source_id = str(payload.get("source_id", "") or "").strip()
        sequence_id = str(payload.get("sequence_id", "") or "").strip()
        source_lease_id = str(payload.get("source_lease_id", "") or "").strip()
        try:
            source_generation = int(payload.get("source_generation", 0))
            capture_epoch_ms = int(payload.get("capture_epoch_ms", 0))
            source_surface_width = int(
                payload.get("source_surface_width", 0)
            )
            source_surface_height = int(
                payload.get("source_surface_height", 0)
            )
            transport_frame_age_ms = int(
                payload.get("transport_frame_age_ms", 0)
            )
            decoder_frame_age_ms = int(
                payload.get("decoder_frame_age_ms", 0)
            )
            presented_frames = int(payload.get("presented_frames", 0))
            media_time = float(payload.get("media_time", 0.0))
        except (TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Source heartbeat generation, epochs, dimensions, ages, and counters must be numeric.",
            ) from exc
        _require_scope_allowed(auth_context, session_id, source_id, "", "")
        if (
            not source_id
            or len(source_id) > 128
            or not sequence_id
            or len(sequence_id) > 192
            or source_generation <= 0
            or not source_lease_id
            or capture_epoch_ms <= 0
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "A current source_id, sequence_id, source_generation, "
                    "source_lease_id, and capture_epoch_ms are required for heartbeat."
                ),
            )
        roi_normalized = payload.get("roi_normalized", [])
        if not isinstance(roi_normalized, (Mapping, list, tuple)):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="roi_normalized must be a four-value vector or geometry object.",
            )
        roi_source_pixels = payload.get("roi_source_pixels", {})
        if not isinstance(roi_source_pixels, Mapping):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="roi_source_pixels must be an object.",
            )
        identity_observation_v3 = payload.get("identity_observation_v3")
        if identity_observation_v3 is not None and not isinstance(
            identity_observation_v3, Mapping
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="identity_observation_v3 must be an object or null.",
            )
        try:
            source_state = await run_in_threadpool(
                get_tracker().heartbeat_external_source,
                session_id,
                source_id=source_id,
                sequence_id=sequence_id,
                source_generation=source_generation,
                source_lease_id=source_lease_id,
                capture_epoch_ms=capture_epoch_ms,
                source_render_fresh=payload.get("source_render_fresh") is True,
                material_change_pending=payload.get("material_change_pending") is True,
                roi_normalized=roi_normalized,
                roi_source_pixels=dict(cast(Mapping[str, Any], roi_source_pixels)),
                source_surface_width=source_surface_width,
                source_surface_height=source_surface_height,
                transport_frame_age_ms=transport_frame_age_ms,
                decoder_frame_age_ms=decoder_frame_age_ms,
                capture_health_reason=str(payload.get("capture_health_reason", "") or ""),
                capture_status=str(payload.get("capture_status", "") or ""),
                presented_frames=presented_frames,
                media_time=media_time,
                identity_observation_v3=(
                    dict(cast(Mapping[str, Any], identity_observation_v3))
                    if isinstance(identity_observation_v3, Mapping)
                    else None
                ),
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Window tracker session not found.",
            ) from exc
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except Exception as exc:
            lease_status = int(getattr(exc, "status_code", 0) or 0)
            lease_reason = str(getattr(exc, "reason_code", "") or "")
            if lease_status in {
                status.HTTP_409_CONFLICT,
                status.HTTP_410_GONE,
            } and lease_reason:
                detail_factory = getattr(exc, "as_detail", None)
                detail = detail_factory() if callable(detail_factory) else {
                    "reason_code": lease_reason,
                    "message": str(getattr(exc, "message", "") or str(exc)),
                }
                raise HTTPException(status_code=lease_status, detail=detail) from exc
            raise
        if (
            str(source_state.get("state", "") or "").strip().upper()
            == "NO_SOURCE"
            and str(source_state.get("reason_code", "") or "").strip().upper()
            == "SOURCE_RECLAIM_REQUIRED"
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "reason_code": "SOURCE_RECLAIM_REQUIRED",
                    "message": (
                        "The failed source lease was released. Reclaim the "
                        "locked chart and submit one fresh frame."
                    ),
                },
            )
        return {
            "schema_version": "PG_CAPTURE_SOURCE_HEARTBEAT_ACCEPTED_V1",
            "accepted": True,
            "session_id": session_id,
            "source_control": _public_source_control(source_state),
        }

    def kill_source_control(
        request: Request,
        session_id: str,
        payload: dict[str, Any] = Body(default={}),
        authorization: str | None = Header(default=None),
        x_phoenixguard_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        _require_origin_allowed(request.headers.get("origin"))
        auth_context = _require_ingest_token(authorization, x_phoenixguard_token)
        source_id = str(payload.get("source_id", "") or "").strip()
        sequence_id = str(payload.get("sequence_id", "") or "").strip()
        source_generation = int(payload.get("source_generation", 0) or 0)
        source_lease_id = str(payload.get("source_lease_id", "") or "").strip()
        _require_scope_allowed(auth_context, session_id, source_id, "", "")
        if not source_id or not sequence_id or source_generation <= 0 or not source_lease_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="A current source_id, sequence_id, source_generation, and source_lease_id are required to stop capture.",
            )
        validation = get_tracker().validate_external_source_lease(
            session_id,
            source_id=source_id,
            sequence_id=sequence_id,
            source_generation=source_generation,
            source_lease_id=source_lease_id,
        )
        if not bool(validation.get("allowed", False)):
            raise HTTPException(
                status_code=int(validation.get("status_code", status.HTTP_409_CONFLICT) or status.HTTP_409_CONFLICT),
                detail={
                    "reason_code": str(validation.get("reason_code", "SOURCE_SUPERSEDED") or "SOURCE_SUPERSEDED"),
                    "message": str(validation.get("message", "Source lease is not current.") or "Source lease is not current."),
                },
            )
        reason = str(payload.get("reason", "Capture stopped by the operator.") or "Capture stopped by the operator.")[:240]
        try:
            state = get_tracker().kill_external_source(
                session_id,
                reason=reason,
                source_id=source_id,
                sequence_id=sequence_id,
                source_generation=source_generation,
                source_lease_id=source_lease_id,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except Exception as exc:
            lease_status = int(getattr(exc, "status_code", 0) or 0)
            lease_reason = str(getattr(exc, "reason_code", "") or "")
            if lease_status in {status.HTTP_409_CONFLICT, status.HTTP_410_GONE} and lease_reason:
                detail_factory = getattr(exc, "as_detail", None)
                detail = detail_factory() if callable(detail_factory) else {
                    "reason_code": lease_reason,
                    "message": str(getattr(exc, "message", "") or str(exc)),
                }
                raise HTTPException(status_code=lease_status, detail=detail) from exc
            raise
        _invalidate_analysis_mailbox_for_session(session_id)
        _retire_feed_runtime_for_session(session_id)
        _security_audit(
            "frame_source_killed",
            auth_context,
            {
                "session_id": session_id,
                "source_id": source_id,
                "source_generation": source_generation,
                "reason": reason,
            },
        )
        return {
            "schema_version": "PG_CAPTURE_SOURCE_KILLED_V1",
            "accepted": True,
            "session_id": session_id,
            "source_control": _public_source_control(state),
        }

    def get_source_control(
        session_id: str,
        authorization: str | None = Header(default=None),
        x_phoenixguard_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        auth_context = _require_ingest_token(authorization, x_phoenixguard_token)
        _require_session_scope_allowed(auth_context, session_id)
        try:
            session = get_tracker().get_session_snapshot(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        return {
            "schema_version": "PG_CAPTURE_SOURCE_STATUS_V1",
            "session_id": session_id,
            "source_control": _public_source_control(session.get("capture_source_v3", {})),
        }

    async def ingest_session_frame(
        request: Request,
        session_id: str,
        frame: UploadFile = File(...),
        source_id: str = Form("edge-agent"),
        symbol: str = Form(""),
        timeframe: str = Form(""),
        source_url: str = Form(""),
        sequence_id: str = Form(""),
        capture_epoch_ms: int = Form(0),
        frame_id: int = Form(0),
        source_generation: int = Form(0),
        source_lease_id: str = Form(""),
        metadata_json: str = Form("{}"),
        authorization: str | None = Header(default=None),
        x_phoenixguard_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        auth_context: FeedAuthContext | None = None
        feed_reservation: FeedRuntimeReservation | None = None
        feed_reservation_committed = False
        decoded_image: Image.Image | None = None
        analysis_job_enqueued = False
        audit_base: dict[str, object] = {
            "session_id": session_id,
            "source_id": source_id,
            "sequence_id": sequence_id,
            "symbol": symbol,
            "timeframe": timeframe,
            "frame_id": int(frame_id or 0),
            "capture_epoch_ms": int(capture_epoch_ms or 0),
            "source_generation": int(source_generation or 0),
            "client_host": request.client.host if request.client else "",
        }
        try:
            _require_origin_allowed(request.headers.get("origin"))
            auth_context = _require_ingest_token(authorization, x_phoenixguard_token)
            metadata = _metadata_from_json(metadata_json)
            effective_symbol, effective_timeframe, identity_hints_ignored = (
                _effective_frame_identity_hints(symbol, timeframe, metadata)
            )
            audit_base["identity_hint_policy"] = (
                "visual_reproof_required"
                if (
                    str(metadata.get("source_type", "") or "").strip(),
                    str(metadata.get("coordinate_space", "") or "").strip(),
                )
                in VISUAL_IDENTITY_SOURCE_CONTRACTS
                else "declared_hint_allowed"
            )
            audit_base["identity_hints_ignored"] = identity_hints_ignored
            _require_scope_allowed(
                auth_context,
                session_id,
                source_id,
                effective_symbol,
                effective_timeframe,
            )
            _require_current_frame_source_contract(
                session_id,
                source_id=source_id,
                sequence_id=sequence_id,
                source_generation=int(source_generation or 0),
                source_lease_id=str(source_lease_id or ""),
                metadata=metadata,
            )
            image, frame_sha256, frame_bytes = await _read_image_upload(frame)
            decoded_image = image
            audit_base["frame_sha256"] = frame_sha256
            audit_base["frame_bytes"] = frame_bytes
            _require_frame_signature(
                auth_context,
                request,
                session_id=session_id,
                source_id=source_id,
                sequence_id=sequence_id,
                capture_epoch_ms=int(capture_epoch_ms or 0),
                frame_id=int(frame_id or 0),
                frame_sha256=frame_sha256,
            )
            feed_reservation = _reserve_feed_runtime(
                auth_context,
                session_id=session_id,
                source_id=source_id,
                sequence_id=sequence_id,
                source_generation=int(source_generation or 0),
                capture_epoch_ms=int(capture_epoch_ms or 0),
                frame_id=int(frame_id or 0),
            )
            metadata["client_host"] = request.client.host if request.client else ""
            metadata["filename"] = frame.filename or ""
            metadata["feed_token_name"] = auth_context.token_name
            metadata["feed_user_id"] = auth_context.user_id
            metadata["frame_sha256"] = frame_sha256
            metadata["frame_bytes"] = frame_bytes
            metadata["source_generation"] = int(source_generation or 0)
            metadata["source_lease_id"] = str(source_lease_id or "")
            if (
                str(metadata.get("source_type", "") or "").strip(),
                str(metadata.get("coordinate_space", "") or "").strip(),
            ) in VISUAL_IDENTITY_SOURCE_CONTRACTS:
                metadata["identity_hint_policy"] = "visual_reproof_required"
                metadata["identity_hints_ignored"] = identity_hints_ignored
            identity_key = _analysis_identity_key(
                source_id=source_id,
                symbol=effective_symbol,
                timeframe=effective_timeframe,
                sequence_id=sequence_id,
                source_generation=int(source_generation or 0),
                metadata=metadata,
            )
            job = FrameAnalysisJob(
                session_id=str(session_id or "").strip(),
                image=image,
                source_id=str(source_id or "").strip(),
                symbol=str(effective_symbol or "").strip(),
                timeframe=str(effective_timeframe or "").strip(),
                source_url=str(source_url or "").strip(),
                sequence_id=str(sequence_id or "").strip(),
                capture_epoch_ms=int(capture_epoch_ms or 0),
                frame_id=int(frame_id or 0),
                source_generation=int(source_generation or 0),
                source_lease_id=str(source_lease_id or "").strip(),
                metadata=metadata,
                tracker=get_tracker(),
                audit_context=_analysis_audit_context(auth_context),
                audit_fields=dict(audit_base),
                identity_key=identity_key,
            )
            mailbox, analysis_disposition, last_result = _enqueue_analysis_job(
                job,
                retry_after_ms=int(auth_context.min_interval_sec) * 1000,
            )
            analysis_job_enqueued = True
            feed_reservation_committed = True
            _security_audit(
                "frame_ingest_accepted",
                auth_context,
                {
                    **audit_base,
                    "analysis_disposition": analysis_disposition,
                    "analysis_busy": mailbox["analysis_busy"],
                    "pending_frame_id": mailbox["pending_frame_id"],
                },
            )
            previous_external_feed = last_result.get("external_frame_feed", {})
            external_feed = (
                dict(cast(Mapping[str, Any], previous_external_feed))
                if isinstance(previous_external_feed, Mapping)
                else {}
            )
            external_feed.update(
                {
                    "source_id": str(source_id or "").strip(),
                    "symbol": str(effective_symbol or "").strip(),
                    "timeframe": str(effective_timeframe or "").strip(),
                    "sequence_id": str(sequence_id or "").strip(),
                    "frame_id": int(frame_id or 0),
                    "capture_epoch_ms": int(capture_epoch_ms or 0),
                    "source_generation": int(source_generation or 0),
                    "analysis_queued": True,
                    "analysis_disposition": analysis_disposition,
                }
            )
            return {
                "schema_version": "PG_FRAME_INGEST_ACCEPTED_V1",
                "accepted": True,
                "session_id": str(last_result.get("session_id", session_id) or session_id),
                "status": str(last_result.get("status", "analysis_queued") or "analysis_queued"),
                "capture_count": int(last_result.get("capture_count", 0) or 0),
                "frame_index": int(last_result.get("frame_index", 0) or 0),
                "state_version": int(last_result.get("state_version", 0) or 0),
                "decision_version": int(last_result.get("decision_version", 0) or 0),
                "external_frame_feed": external_feed,
                "analysis_disposition": analysis_disposition,
                "analysis_mailbox": mailbox,
                "analysis_busy": mailbox["analysis_busy"],
                "active_frame_id": mailbox["active_frame_id"],
                "active_superseded": mailbox["active_superseded"],
                "pending_frame_id": mailbox["pending_frame_id"],
                "replaced_frame_count": mailbox["replaced_frame_count"],
                "retry_after_ms": mailbox["retry_after_ms"],
                "last_completed_frame_id": mailbox["last_completed_frame_id"],
                "last_completed_epoch_ms": mailbox["last_completed_epoch_ms"],
                "last_failed_frame_id": mailbox["last_failed_frame_id"],
                "last_failed_epoch_ms": mailbox["last_failed_epoch_ms"],
                "last_failure_reason_code": mailbox["last_failure_reason_code"],
                "last_failure_error_type": mailbox["last_failure_error_type"],
            }
        except HTTPException as exc:
            _security_audit(
                "frame_ingest_rejected",
                auth_context,
                {**audit_base, "status_code": exc.status_code, "detail": str(exc.detail)},
            )
            raise
        except KeyError as exc:
            _security_audit(
                "frame_ingest_rejected",
                auth_context,
                {**audit_base, "status_code": status.HTTP_404_NOT_FOUND, "detail": "Window tracker session not found."},
            )
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            message = str(exc)
            lease_reason = ""
            lease_status = 0
            if "SOURCE_KILLED" in message:
                lease_reason = "SOURCE_KILLED"
                lease_status = status.HTTP_410_GONE
            elif "SOURCE_SUPERSEDED" in message:
                lease_reason = "SOURCE_SUPERSEDED"
                lease_status = status.HTTP_409_CONFLICT
            if lease_status:
                detail = {"reason_code": lease_reason, "message": message}
                _security_audit(
                    "frame_ingest_rejected",
                    auth_context,
                    {**audit_base, "status_code": lease_status, "detail": message},
                )
                raise HTTPException(status_code=lease_status, detail=detail) from exc
            _security_audit(
                "frame_ingest_rejected",
                auth_context,
                {**audit_base, "status_code": status.HTTP_400_BAD_REQUEST, "detail": message},
            )
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc
        except Exception as exc:
            lease_status = int(getattr(exc, "status_code", 0) or 0)
            lease_reason = str(getattr(exc, "reason_code", "") or "")
            if lease_status in {status.HTTP_409_CONFLICT, status.HTTP_410_GONE} and lease_reason:
                message = str(getattr(exc, "message", "") or str(exc))
                detail_factory = getattr(exc, "as_detail", None)
                detail = detail_factory() if callable(detail_factory) else {
                    "reason_code": lease_reason,
                    "message": message,
                }
                _security_audit(
                    "frame_ingest_rejected",
                    auth_context,
                    {**audit_base, "status_code": lease_status, "detail": message},
                )
                raise HTTPException(status_code=lease_status, detail=detail) from exc
            raise
        finally:
            if not feed_reservation_committed:
                _rollback_feed_runtime_reservation(feed_reservation)
            if decoded_image is not None and not analysis_job_enqueued:
                decoded_image.close()

    def frame_ingest_status(
        session_id: str,
        authorization: str | None = Header(default=None),
        x_phoenixguard_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        auth_context = _require_ingest_token(authorization, x_phoenixguard_token)
        _require_session_scope_allowed(auth_context, session_id)
        mailbox, last_result = _analysis_mailbox_snapshot(
            session_id,
            default_retry_after_ms=int(auth_context.min_interval_sec) * 1000,
        )
        tracker = get_tracker()
        transport_state: dict[str, Any] = {}
        transport_getter = getattr(
            tracker,
            "get_external_frame_transport_status",
            None,
        )
        if callable(transport_getter):
            try:
                transport_value = transport_getter(session_id)
                if isinstance(transport_value, Mapping):
                    transport_state = dict(
                        cast(Mapping[str, Any], transport_value)
                    )
            except KeyError:
                transport_state = {}
            except Exception:
                # Status must remain available even if bounded transport
                # telemetry is temporarily unreadable.
                transport_state = {}
        if bool(mailbox["analysis_busy"]):
            session = dict(last_result)
        else:
            try:
                session = tracker.get_session_snapshot(session_id)
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        feed = session.get("external_frame_feed")
        feed_payload = dict(cast(Mapping[str, Any], feed)) if isinstance(feed, Mapping) else {}
        if transport_state:
            for key in (
                "source_id",
                "source_type",
                "sequence_id",
                "source_generation",
                "coordinate_space",
            ):
                if key in transport_state:
                    feed_payload[key] = transport_state.get(key)
            if "last_frame_id" in transport_state:
                feed_payload["frame_id"] = transport_state.get("last_frame_id")
            transport_stream = transport_state.get("stream", {})
            if isinstance(transport_stream, Mapping):
                feed_payload["transport_stream"] = dict(
                    cast(Mapping[str, Any], transport_stream)
                )
        source_state = str(
            transport_state.get("source_state", "") or ""
        ).strip().upper()
        transport_stream_value = transport_state.get("stream", {})
        transport_stream = (
            cast(Mapping[str, Any], transport_stream_value)
            if isinstance(transport_stream_value, Mapping)
            else cast(Mapping[str, Any], {})
        )
        effective_capture_count = max(
            0,
            int(session.get("capture_count", 0) or 0),
            int(transport_stream.get("received_frames", 0) or 0),
        )
        effective_frame_index = max(
            0,
            int(session.get("frame_index", 0) or 0),
            int(transport_state.get("last_frame_id", 0) or 0),
            int(transport_stream.get("processing_frame_id", 0) or 0),
        )
        effective_status = str(session.get("status", "") or "")
        if not effective_status and bool(mailbox["analysis_busy"]):
            effective_status = "analysis_queued"
        if source_state == "ERROR":
            effective_status = "external_source_error"
        elif bool(mailbox["analysis_busy"]) and source_state == "VALIDATING":
            effective_status = "external_source_validating"
        return {
            "schema_version": "PG_FRAME_INGEST_STATUS_V1",
            "session_id": session_id,
            "status": effective_status,
            "capture_count": effective_capture_count,
            "frame_index": effective_frame_index,
            "last_capture_at": session.get("last_capture_at", ""),
            "external_frame_feed": feed_payload,
            "transport_state": transport_state,
            "analysis_mailbox": mailbox,
            "analysis_busy": mailbox["analysis_busy"],
            "active_frame_id": mailbox["active_frame_id"],
            "active_superseded": mailbox["active_superseded"],
            "pending_frame_id": mailbox["pending_frame_id"],
            "replaced_frame_count": mailbox["replaced_frame_count"],
            "retry_after_ms": mailbox["retry_after_ms"],
            "last_completed_frame_id": mailbox["last_completed_frame_id"],
            "last_completed_epoch_ms": mailbox["last_completed_epoch_ms"],
            "last_failed_frame_id": mailbox["last_failed_frame_id"],
            "last_failed_epoch_ms": mailbox["last_failed_epoch_ms"],
            "last_failure_reason_code": mailbox["last_failure_reason_code"],
            "last_failure_error_type": mailbox["last_failure_error_type"],
        }

    router.add_api_route("/config", frame_ingest_config, methods=["GET"])
    router.add_api_route("/readiness", frame_ingest_readiness, methods=["GET"])
    router.add_api_route("/mobile-uploader", mobile_frame_uploader, methods=["GET"], response_class=HTMLResponse)
    router.add_api_route(
        "/sessions/{session_id}/source-control/claim",
        claim_source_control,
        methods=["POST"],
        status_code=status.HTTP_201_CREATED,
    )
    router.add_api_route(
        "/sessions/{session_id}/source-control/kill",
        kill_source_control,
        methods=["POST"],
    )
    router.add_api_route(
        "/sessions/{session_id}/source-control/heartbeat",
        heartbeat_source_control,
        methods=["POST"],
    )
    router.add_api_route(
        "/sessions/{session_id}/source-control",
        get_source_control,
        methods=["GET"],
    )
    router.add_api_route(
        "/sessions/{session_id}/frames",
        ingest_session_frame,
        methods=["POST"],
        status_code=status.HTTP_202_ACCEPTED,
    )
    router.add_api_route("/sessions/{session_id}/status", frame_ingest_status, methods=["GET"])
    return router
