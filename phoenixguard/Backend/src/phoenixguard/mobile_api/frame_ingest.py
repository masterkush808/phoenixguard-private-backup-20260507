from __future__ import annotations

from collections.abc import Callable, Mapping
from io import BytesIO
import json
import os
import secrets
from typing import Any, Protocol, cast

from fastapi import APIRouter, File, Form, Header, HTTPException, Request, UploadFile, status
from PIL import Image


DEFAULT_MAX_FRAME_BYTES = 15 * 1024 * 1024
DEFAULT_MIN_FRAME_SIDE = 64
DEFAULT_MAX_FRAME_SIDE = 8192


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


def _configured_ingest_token() -> str:
    return str(os.getenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "") or "").strip()


def _submitted_token(authorization: str | None, x_phoenixguard_token: str | None) -> str:
    auth = str(authorization or "").strip()
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return str(x_phoenixguard_token or "").strip()


def _require_ingest_token(authorization: str | None, x_phoenixguard_token: str | None) -> None:
    expected = _configured_ingest_token()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Frame ingest is not armed. Set PHOENIXGUARD_FRAME_INGEST_TOKEN before exposing this endpoint.",
        )
    supplied = _submitted_token(authorization, x_phoenixguard_token)
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid frame ingest token.")


def _metadata_from_json(raw_json: str) -> dict[str, Any]:
    text = str(raw_json or "").strip()
    if not text:
        return {}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata_json is not valid JSON.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="metadata_json must be a JSON object.")
    parsed_mapping = cast(Mapping[str, Any], parsed)
    return {str(key): value for key, value in parsed_mapping.items()}


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
        return {
            "schema_version": "PG_FRAME_INGEST_CONFIG_V1",
            "token_required": True,
            "max_frame_bytes": _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_BYTES", DEFAULT_MAX_FRAME_BYTES, 1024 * 1024),
            "min_frame_side": _env_int("PHOENIXGUARD_FRAME_INGEST_MIN_SIDE", DEFAULT_MIN_FRAME_SIDE, 16),
            "max_frame_side": _env_int("PHOENIXGUARD_FRAME_INGEST_MAX_SIDE", DEFAULT_MAX_FRAME_SIDE, 512),
            "supported_sources": [
                "windows_window_capture",
                "edge_agent_screenshot",
                "browser_extension_capture",
                "cloud_browser_worker",
                "mt4_chart_screenshot",
                "replay_upload",
            ],
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
        metadata_json: str = Form("{}"),
        authorization: str | None = Header(default=None),
        x_phoenixguard_token: str | None = Header(default=None),
    ) -> dict[str, object]:
        _require_ingest_token(authorization, x_phoenixguard_token)
        image = await _read_image_upload(frame)
        metadata = _metadata_from_json(metadata_json)
        metadata.setdefault("client_host", request.client.host if request.client else "")
        metadata.setdefault("filename", frame.filename or "")
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
        _require_ingest_token(authorization, x_phoenixguard_token)
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
    router.add_api_route(
        "/sessions/{session_id}/frames",
        ingest_session_frame,
        methods=["POST"],
        status_code=status.HTTP_202_ACCEPTED,
    )
    router.add_api_route("/sessions/{session_id}/status", frame_ingest_status, methods=["GET"])
    return router
