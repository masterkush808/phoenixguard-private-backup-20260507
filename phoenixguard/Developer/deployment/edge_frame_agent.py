from __future__ import annotations

import argparse
from io import BytesIO
import json
import mimetypes
from pathlib import Path
import sys
import time
from typing import Any, Iterator, Mapping, cast
from urllib import error, request
from uuid import uuid4

from PIL import Image


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
) -> dict[str, object]:
    endpoint = f"{base_url.rstrip('/')}/v1/mobile/frame-ingest/sessions/{session_id}/frames"
    fields = {
        "source_id": source_id,
        "source_url": source_url,
        "symbol": symbol,
        "timeframe": timeframe,
        "sequence_id": sequence_id,
        "capture_epoch_ms": str(int(round(time.time() * 1000.0))),
        "frame_id": str(frame_id),
        "metadata_json": json.dumps({"agent": "edge_frame_agent.py", "filename": filename}, separators=(",", ":")),
    }
    body, content_type = _multipart_body(fields, "frame", filename, frame_bytes)
    req = request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "User-Agent": "PhoenixGuardEdgeFrameAgent/1.0",
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
    parser.add_argument("--base-url", required=True, help="PhoenixGuard API base URL, for example https://pg.example.com")
    parser.add_argument("--session-id", default="external-live", help="Tracker session id to feed.")
    parser.add_argument("--token", default="", help="Frame ingest token. Defaults to PHOENIXGUARD_FRAME_INGEST_TOKEN.")
    parser.add_argument("--source-id", default="edge-agent", help="Stable feed identity for source locking.")
    parser.add_argument("--source-url", default="", help="Broker/chart URL being represented by the frame feed.")
    parser.add_argument("--symbol", default="", help="Optional symbol label, for example EURCAD.")
    parser.add_argument("--timeframe", default="", help="Optional timeframe label, for example M5.")
    parser.add_argument("--sequence-id", default="", help="Optional stable sequence id. Defaults to source-id.")
    parser.add_argument("--interval-sec", type=float, default=15.0, help="Seconds between frame uploads in screen mode.")
    parser.add_argument("--bbox", default="", help="Screen capture crop: left,top,right,bottom. Omit for full screen.")
    parser.add_argument("--image-dir", default="", help="Read frames from this folder instead of screen capture.")
    parser.add_argument("--once", action="store_true", help="Upload one frame and exit.")
    parser.add_argument("--timeout-sec", type=float, default=30.0, help="HTTP upload timeout.")
    args = parser.parse_args()

    token = str(args.token or "").strip()
    if not token:
        import os

        token = str(os.getenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "") or "").strip()
    if not token:
        print("Missing ingest token. Pass --token or set PHOENIXGUARD_FRAME_INGEST_TOKEN.", file=sys.stderr)
        return 2

    bbox = _parse_bbox(args.bbox)
    sequence_id = str(args.sequence_id or args.source_id or "edge-agent")
    image_dir = Path(args.image_dir).expanduser() if str(args.image_dir or "").strip() else None
    image_iter = _iter_images(image_dir) if image_dir else None
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
                base_url=args.base_url,
                session_id=args.session_id,
                token=token,
                frame_bytes=frame_bytes,
                filename=filename,
                source_id=args.source_id,
                source_url=args.source_url,
                symbol=args.symbol,
                timeframe=args.timeframe,
                sequence_id=sequence_id,
                frame_id=frame_id,
                timeout_sec=args.timeout_sec,
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
        time.sleep(max(1.0, float(args.interval_sec or 15.0)))


if __name__ == "__main__":
    raise SystemExit(main())
