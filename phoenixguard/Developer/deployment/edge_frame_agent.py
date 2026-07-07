from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import hashlib
import hmac
from io import BytesIO
import json
import mimetypes
import os
from pathlib import Path
import sys
import time
from typing import Any, Iterator, Mapping, cast
from urllib import error, parse, request
from uuid import uuid4

from PIL import Image


def _empty_metadata() -> dict[str, Any]:
    return {}


@dataclass(slots=True)
class AgentProfile:
    base_url: str = ""
    session_id: str = "external-live"
    token: str = ""
    signing_secret: str = ""
    source_id: str = "edge-agent"
    source_type: str = "pc_screen_capture"
    source_url: str = ""
    symbol: str = ""
    timeframe: str = ""
    sequence_id: str = ""
    interval_sec: float = 15.0
    bbox: str = ""
    image_dir: str = ""
    timeout_sec: float = 30.0
    user_id: str = ""
    device_id: str = ""
    metadata: dict[str, Any] = field(default_factory=_empty_metadata)


_PROFILE_FIELDS: tuple[str, ...] = (
    "base_url",
    "session_id",
    "token",
    "signing_secret",
    "source_id",
    "source_type",
    "source_url",
    "symbol",
    "timeframe",
    "sequence_id",
    "interval_sec",
    "bbox",
    "image_dir",
    "timeout_sec",
    "user_id",
    "device_id",
    "metadata",
)


def _parse_bbox(raw: str) -> tuple[int, int, int, int] | None:
    text = str(raw or "").strip()
    if not text:
        return None
    parts = [part.strip() for part in text.replace(";", ",").split(",")]
    if len(parts) != 4:
        raise ValueError("--bbox must be formatted as left,top,right,bottom")
    left, top, right, bottom = [int(float(part)) for part in parts]
    if right <= left or bottom <= top:
        raise ValueError("--bbox right/bottom must be larger than left/top")
    return left, top, right, bottom


def _load_profile(path: Path, profile_name: str) -> AgentProfile:
    try:
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read feed profile config: {path}") from exc
    if not isinstance(parsed, Mapping):
        raise ValueError("Feed profile config must be a JSON object.")
    parsed_mapping = cast(Mapping[str, object], parsed)
    profiles_obj = parsed_mapping.get("profiles", {})
    if not isinstance(profiles_obj, Mapping):
        raise ValueError("Feed profile config must contain a profiles object.")
    profiles = cast(Mapping[str, object], profiles_obj)
    selected_name = profile_name or str(parsed_mapping.get("default_profile") or "").strip()
    if not selected_name:
        raise ValueError("Pass --profile or set default_profile in the feed profile config.")
    selected_obj = profiles.get(selected_name)
    if not isinstance(selected_obj, Mapping):
        raise ValueError(f"Feed profile not found: {selected_name}")
    selected = cast(Mapping[str, object], selected_obj)
    profile = AgentProfile()
    for field_name in _PROFILE_FIELDS:
        if field_name not in selected:
            continue
        value = selected[field_name]
        if field_name == "metadata":
            profile.metadata = dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}
        elif field_name in {"interval_sec", "timeout_sec"}:
            setattr(profile, field_name, float(str(value or "0")))
        else:
            setattr(profile, field_name, str(value or ""))
    return profile


def _metadata_from_json(raw_json: str) -> dict[str, Any]:
    text = str(raw_json or "").strip()
    if not text:
        return {}
    parsed = json.loads(text)
    if not isinstance(parsed, Mapping):
        raise ValueError("--metadata-json must be a JSON object.")
    return {str(key): value for key, value in cast(Mapping[str, Any], parsed).items()}


def _iter_images(image_dir: Path) -> Iterator[Path]:
    suffixes = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
    while True:
        files = sorted(
            (path for path in image_dir.glob("*") if path.is_file() and path.suffix.lower() in suffixes),
            key=lambda path: (path.stat().st_mtime, path.name),
        )
        for path in files:
            yield path
        time.sleep(0.25)


def _capture_screen_png(bbox: tuple[int, int, int, int] | None) -> bytes:
    try:
        from PIL import ImageGrab
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise RuntimeError("Pillow ImageGrab is required for screen capture mode.") from exc
    image = ImageGrab.grab(bbox=bbox).convert("RGB")
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _load_image_bytes(path: Path) -> tuple[bytes, str]:
    with Image.open(path) as image:
        output = BytesIO()
        image.convert("RGB").save(output, format="PNG")
    return output.getvalue(), path.name


def _multipart_body(fields: dict[str, str], file_field: str, filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = f"----PhoenixGuardFrame{uuid4().hex}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("ascii"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    content_type = mimetypes.guess_type(filename)[0] or "image/png"
    chunks.append(f"--{boundary}\r\n".encode("ascii"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="{file_field}"; filename="{filename}"\r\n'
            f"Content-Type: {content_type}\r\n\r\n"
        ).encode("ascii")
    )
    chunks.append(data)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _canonical_frame_signature_payload(
    *,
    method: str,
    path: str,
    session_id: str,
    source_id: str,
    sequence_id: str,
    frame_id: int,
    capture_epoch_ms: str,
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
            str(int(capture_epoch_ms or "0")),
            str(frame_sha256 or "").strip().lower(),
            str(timestamp or "").strip(),
            str(nonce or "").strip(),
        ]
    )


def _signature_headers(
    *,
    endpoint: str,
    session_id: str,
    source_id: str,
    sequence_id: str,
    frame_id: int,
    capture_epoch_ms: str,
    frame_bytes: bytes,
    signing_secret: str,
) -> dict[str, str]:
    if not signing_secret:
        return {}
    timestamp = str(int(round(time.time() * 1000.0)))
    nonce = uuid4().hex
    frame_sha256 = hashlib.sha256(frame_bytes).hexdigest()
    canonical = _canonical_frame_signature_payload(
        method="POST",
        path=parse.urlparse(endpoint).path,
        session_id=session_id,
        source_id=source_id,
        sequence_id=sequence_id,
        frame_id=frame_id,
        capture_epoch_ms=capture_epoch_ms,
        frame_sha256=frame_sha256,
        timestamp=timestamp,
        nonce=nonce,
    )
    signature = hmac.new(signing_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "X-PhoenixGuard-Signature-Alg": "HMAC-SHA256-V1",
        "X-PhoenixGuard-Timestamp": timestamp,
        "X-PhoenixGuard-Nonce": nonce,
        "X-PhoenixGuard-Signature": f"v1={signature}",
    }


def _post_frame(
    *,
    base_url: str,
    session_id: str,
    token: str,
    frame_bytes: bytes,
    filename: str,
    source_id: str,
    source_url: str,
    symbol: str,
    timeframe: str,
    sequence_id: str,
    frame_id: int,
    timeout_sec: float,
    metadata: Mapping[str, Any],
    signing_secret: str = "",
) -> dict[str, object]:
    endpoint = f"{base_url.rstrip('/')}/v1/mobile/frame-ingest/sessions/{session_id}/frames"
    capture_epoch_ms = str(int(round(time.time() * 1000.0)))
    fields = {
        "source_id": source_id,
        "source_url": source_url,
        "symbol": symbol,
        "timeframe": timeframe,
        "sequence_id": sequence_id,
        "capture_epoch_ms": capture_epoch_ms,
        "frame_id": str(frame_id),
        "metadata_json": json.dumps(
            {"agent": "edge_frame_agent.py", "filename": filename, **dict(metadata)},
            separators=(",", ":"),
        ),
    }
    body, content_type = _multipart_body(fields, "frame", filename, frame_bytes)
    req = request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "User-Agent": "PhoenixGuardEdgeFrameAgent/1.0",
            **_signature_headers(
                endpoint=endpoint,
                session_id=session_id,
                source_id=source_id,
                sequence_id=sequence_id,
                frame_id=frame_id,
                capture_epoch_ms=capture_epoch_ms,
                frame_bytes=frame_bytes,
                signing_secret=signing_secret,
            ),
        },
        method="POST",
    )
    with request.urlopen(req, timeout=max(1.0, float(timeout_sec))) as response:
        payload = response.read().decode("utf-8", errors="replace")
    decoded: object = json.loads(payload) if payload.strip() else {}
    if isinstance(decoded, dict):
        decoded_mapping = cast(Mapping[str, Any], decoded)
        return {str(key): value for key, value in decoded_mapping.items()}
    return {"response": decoded}


def main() -> int:
    parser = argparse.ArgumentParser(description="Push chart frames into a PhoenixGuard cloud brain.")
    parser.add_argument("--config", default="", help="Optional JSON file containing feed profiles.")
    parser.add_argument("--profile", default="", help="Profile name inside --config.")
    parser.add_argument("--base-url", default="", help="PhoenixGuard API base URL, for example https://pg.example.com")
    parser.add_argument("--session-id", default="", help="Tracker session id to feed.")
    parser.add_argument("--token", default="", help="Frame ingest token. Defaults to PHOENIXGUARD_FRAME_INGEST_TOKEN.")
    parser.add_argument("--signing-secret", default="", help="Frame HMAC signing secret. Defaults to PHOENIXGUARD_FRAME_INGEST_SIGNING_SECRET.")
    parser.add_argument("--source-id", default="", help="Stable feed identity for source locking.")
    parser.add_argument("--source-type", default="", help="Feed source type, for example pc_screen_capture.")
    parser.add_argument("--source-url", default="", help="Broker/chart URL being represented by the frame feed.")
    parser.add_argument("--symbol", default="", help="Optional symbol label, for example EURCAD.")
    parser.add_argument("--timeframe", default="", help="Optional timeframe label, for example M5.")
    parser.add_argument("--sequence-id", default="", help="Optional stable sequence id. Defaults to source-id.")
    parser.add_argument("--interval-sec", type=float, default=0.0, help="Seconds between frame uploads in screen mode.")
    parser.add_argument("--bbox", default="", help="Screen capture crop: left,top,right,bottom. Omit for full screen.")
    parser.add_argument("--image-dir", default="", help="Read frames from this folder instead of screen capture.")
    parser.add_argument("--user-id", default="", help="Optional platform user id for server-side feed tracing.")
    parser.add_argument("--device-id", default="", help="Optional device id for server-side feed tracing.")
    parser.add_argument("--metadata-json", default="{}", help="Extra JSON object sent with every frame.")
    parser.add_argument("--once", action="store_true", help="Upload one frame and exit.")
    parser.add_argument("--timeout-sec", type=float, default=0.0, help="HTTP upload timeout.")
    args = parser.parse_args()

    profile = AgentProfile()
    if str(args.config or "").strip():
        profile = _load_profile(Path(str(args.config)).expanduser(), str(args.profile or "").strip())

    def pick_text(arg_value: object, profile_value: str, default: str = "") -> str:
        return str(arg_value or profile_value or default).strip()

    def pick_float(arg_value: float, profile_value: float, default: float) -> float:
        return float(arg_value or profile_value or default)

    base_url = pick_text(args.base_url, profile.base_url)
    if not base_url:
        print("Missing --base-url or profile base_url.", file=sys.stderr)
        return 2
    session_id = pick_text(args.session_id, profile.session_id, "external-live")
    source_id = pick_text(args.source_id, profile.source_id, "edge-agent")
    source_type = pick_text(args.source_type, profile.source_type, "pc_screen_capture")
    source_url = pick_text(args.source_url, profile.source_url)
    symbol = pick_text(args.symbol, profile.symbol)
    timeframe = pick_text(args.timeframe, profile.timeframe)
    interval_sec = pick_float(float(args.interval_sec or 0.0), profile.interval_sec, 15.0)
    timeout_sec = pick_float(float(args.timeout_sec or 0.0), profile.timeout_sec, 30.0)
    bbox_text = pick_text(args.bbox, profile.bbox)
    image_dir_text = pick_text(args.image_dir, profile.image_dir)
    user_id = pick_text(args.user_id, profile.user_id)
    device_id = pick_text(args.device_id, profile.device_id)

    token = str(args.token or "").strip()
    if not token:
        token = profile.token.strip()
    if not token:
        token = str(os.getenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "") or "").strip()
    if not token:
        print("Missing ingest token. Pass --token or set PHOENIXGUARD_FRAME_INGEST_TOKEN.", file=sys.stderr)
        return 2
    signing_secret = pick_text(args.signing_secret, profile.signing_secret)
    if not signing_secret:
        signing_secret = str(os.getenv("PHOENIXGUARD_FRAME_INGEST_SIGNING_SECRET", "") or "").strip()

    bbox = _parse_bbox(bbox_text)
    sequence_id = pick_text(args.sequence_id, profile.sequence_id, source_id)
    image_dir = Path(image_dir_text).expanduser() if image_dir_text else None
    image_iter = _iter_images(image_dir) if image_dir else None
    metadata = {
        **profile.metadata,
        **_metadata_from_json(str(args.metadata_json or "{}")),
        "source_type": source_type,
        "user_id": user_id,
        "device_id": device_id,
        "capture_mode": "image_dir" if image_iter is not None else "screen",
    }
    frame_id = 0
    while True:
        frame_id += 1
        if image_iter is not None:
            image_path = next(image_iter)
            frame_bytes, filename = _load_image_bytes(image_path)
        else:
            frame_bytes = _capture_screen_png(bbox)
            filename = f"frame_{frame_id:08d}.png"
        try:
            result = _post_frame(
                base_url=base_url,
                session_id=session_id,
                token=token,
                frame_bytes=frame_bytes,
                filename=filename,
                source_id=source_id,
                source_url=source_url,
                symbol=symbol,
                timeframe=timeframe,
                sequence_id=sequence_id,
                frame_id=frame_id,
                timeout_sec=timeout_sec,
                metadata=metadata,
                signing_secret=signing_secret,
            )
            print(
                json.dumps(
                    {
                        "frame_id": frame_id,
                        "capture_count": result.get("capture_count"),
                        "state_version": result.get("state_version"),
                        "status": result.get("status"),
                    },
                    separators=(",", ":"),
                ),
                flush=True,
            )
        except (error.HTTPError, error.URLError, TimeoutError, RuntimeError, OSError, ValueError) as exc:
            print(f"upload_failed frame_id={frame_id} error={exc}", file=sys.stderr, flush=True)
        if args.once:
            return 0
        time.sleep(max(1.0, interval_sec))


if __name__ == "__main__":
    raise SystemExit(main())
