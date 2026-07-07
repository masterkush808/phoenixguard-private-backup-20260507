from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Any, Protocol, Sequence, cast

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse
from PIL import Image


DEFAULT_MAX_FRAME_BYTES = 15 * 1024 * 1024
DEFAULT_MIN_FRAME_SIDE = 64
DEFAULT_MAX_FRAME_SIDE = 8192
DEFAULT_MAX_METADATA_BYTES = 8192
DEFAULT_MIN_INTERVAL_SEC = 10
DEFAULT_ACTIVE_FEED_WINDOW_SEC = 240
DEFAULT_MAX_ACTIVE_FEEDS_TOTAL = 3
DEFAULT_MAX_ACTIVE_FEEDS_PER_TOKEN = 1


@dataclass(frozen=True, slots=True)
class FeedAuthContext:
    token_name: str
    user_id: str
    global_token: bool
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
    last_capture_epoch_ms: int
    last_frame_id: int
    last_seen_monotonic: float


_REPO_ROOT = Path(__file__).resolve().parents[4]
_MOBILE_UPLOADER_PATH = _REPO_ROOT / "Frontend" / "dashboard" / "static" / "frame_ingest" / "mobile_frame_uploader.html"
_FEED_STATE_LOCK = threading.Lock()
_FEED_RUNTIME_STATE: dict[str, FeedRuntimeState] = {}


def reset_frame_ingest_runtime_state_for_tests() -> None:
    with _FEED_STATE_LOCK:
        _FEED_RUNTIME_STATE.clear()


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
    return {str(key): value for key, value in parsed_mapping.items()}


def _feed_key(context: FeedAuthContext, session_id: str, source_id: str, sequence_id: str) -> str:
    token_scope = context.token_name if context.global_token else f"{context.token_name}:{context.user_id}"
    return "|".join(
        [
            token_scope,
            str(session_id or "").strip(),
            str(source_id or "").strip(),
            str(sequence_id or "").strip(),
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


def _record_or_reject_feed_runtime(
    context: FeedAuthContext,
    *,
    session_id: str,
    source_id: str,
    sequence_id: str,
    capture_epoch_ms: int,
    frame_id: int,
) -> None:
    if _env_bool("PHOENIXGUARD_FRAME_INGEST_REQUIRE_CAPTURE_EPOCH", True) and int(capture_epoch_ms or 0) <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="capture_epoch_ms is required for live frame ingest.")
    if _env_bool("PHOENIXGUARD_FRAME_INGEST_REQUIRE_FRAME_ID", True) and int(frame_id or 0) <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="frame_id is required for live frame ingest.")
    key = _feed_key(context, session_id, source_id, sequence_id)
    now_monotonic = time.monotonic()
    with _FEED_STATE_LOCK:
        active_states = _active_feed_states(now_monotonic)
        existing = _FEED_RUNTIME_STATE.get(key)
        if existing is not None:
            elapsed = now_monotonic - float(existing.last_seen_monotonic)
            if elapsed < float(context.min_interval_sec):
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Frame feed interval is too fast. Wait at least {context.min_interval_sec} seconds between frames.",
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
        _FEED_RUNTIME_STATE[key] = FeedRuntimeState(
            token_name=context.token_name,
            user_id=context.user_id,
            session_id=str(session_id or "").strip(),
            source_id=str(source_id or "").strip(),
            sequence_id=str(sequence_id or "").strip(),
            last_capture_epoch_ms=int(capture_epoch_ms or 0),
            last_frame_id=int(frame_id or 0),
            last_seen_monotonic=now_monotonic,
        )


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
        "allowed_origins_configured": bool(_env_csv("PHOENIXGUARD_FRAME_INGEST_ALLOWED_ORIGINS")),
    }


async def _read_image_upload(frame: UploadFile) -> Image.Image:
    data = await frame.read()
    max_bytes = _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_BYTES", DEFAULT_MAX_FRAME_BYTES, 1024 * 1024)
    if not data:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Frame upload is empty.")
    if len(data) > max_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Frame upload is too large.")
    try:
        image = Image.open(BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Frame upload is not a readable image.") from exc
    min_side = _env_int("PHOENIXGUARD_FRAME_INGEST_MIN_SIDE", DEFAULT_MIN_FRAME_SIDE, 16)
    max_side = _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_SIDE", DEFAULT_MAX_FRAME_SIDE, 512)
    if image.width < min_side or image.height < min_side:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Frame is too small to be a chart.")
    if image.width > max_side or image.height > max_side:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Frame is larger than the configured ingest limit.")
    return image


def build_frame_ingest_router(get_tracker: Callable[[], FrameIngestTracker]) -> APIRouter:
    router = APIRouter(prefix="/v1/mobile/frame-ingest", tags=["frame-ingest"])

    def frame_ingest_config() -> dict[str, object]:
        readiness = _readiness_payload()
        return {
            "schema_version": "PG_FRAME_INGEST_CONFIG_V1",
            "token_required": True,
            "scoped_tokens_supported": True,
            "token_registry_configured": _token_registry_path() is not None,
            "readiness": readiness,
            "max_frame_bytes": _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_BYTES", DEFAULT_MAX_FRAME_BYTES, 1024 * 1024),
            "min_frame_side": _env_int("PHOENIXGUARD_FRAME_INGEST_MIN_SIDE", DEFAULT_MIN_FRAME_SIDE, 16),
            "max_frame_side": _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_SIDE", DEFAULT_MAX_FRAME_SIDE, 512),
            "min_interval_sec": readiness["min_interval_sec"],
            "max_active_feeds_total": readiness["max_active_feeds_total"],
            "max_active_feeds_per_token": readiness["max_active_feeds_per_token"],
            "supported_sources": [
                "windows_window_capture",
                "edge_agent_screenshot",
                "pc_screen_capture",
                "mobile_manual_upload",
                "mobile_pwa_screen_capture",
                "android_native_capture",
                "ios_replaykit_capture",
                "browser_extension_capture",
                "cloud_browser_worker",
                "mt4_chart_screenshot",
                "replay_upload",
            ],
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
        metadata_json: str = Form("{}"),
        authorization: str | None = Header(default=None),
        x_phoenixguard_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        _require_origin_allowed(request.headers.get("origin"))
        auth_context = _require_ingest_token(authorization, x_phoenixguard_token)
        _require_scope_allowed(auth_context, session_id, source_id, symbol, timeframe)
        _record_or_reject_feed_runtime(
            auth_context,
            session_id=session_id,
            source_id=source_id,
            sequence_id=sequence_id,
            capture_epoch_ms=int(capture_epoch_ms or 0),
            frame_id=int(frame_id or 0),
        )
        image = await _read_image_upload(frame)
        metadata = _metadata_from_json(metadata_json)
        metadata.setdefault("client_host", request.client.host if request.client else "")
        metadata.setdefault("filename", frame.filename or "")
        metadata.setdefault("feed_token_name", auth_context.token_name)
        metadata.setdefault("feed_user_id", auth_context.user_id)
        try:
            return get_tracker().ingest_external_frame(
                session_id,
                image,
                source_id=source_id,
                symbol=symbol,
                timeframe=timeframe,
                source_url=source_url,
                sequence_id=sequence_id,
                capture_epoch_ms=capture_epoch_ms,
                frame_id=frame_id,
                metadata=metadata,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    def frame_ingest_status(
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
        feed = session.get("external_frame_feed")
        feed_payload = dict(cast(Mapping[str, Any], feed)) if isinstance(feed, Mapping) else {}
        return {
            "schema_version": "PG_FRAME_INGEST_STATUS_V1",
            "session_id": session_id,
            "status": session.get("status", ""),
            "capture_count": session.get("capture_count", 0),
            "frame_index": session.get("frame_index", 0),
            "last_capture_at": session.get("last_capture_at", ""),
            "external_frame_feed": feed_payload,
        }

    router.add_api_route("/config", frame_ingest_config, methods=["GET"])
    router.add_api_route("/readiness", frame_ingest_readiness, methods=["GET"])
    router.add_api_route("/mobile-uploader", mobile_frame_uploader, methods=["GET"], response_class=HTMLResponse)
    router.add_api_route(
        "/sessions/{session_id}/frames",
        ingest_session_frame,
        methods=["POST"],
        status_code=status.HTTP_202_ACCEPTED,
    )
    router.add_api_route("/sessions/{session_id}/status", frame_ingest_status, methods=["GET"])
    return router
