from __future__ import annotations

import argparse
import hashlib
import hmac
from io import BytesIO
import json
import time
from typing import Any, Mapping, cast
from urllib import error, parse, request
from uuid import uuid4

from PIL import Image


def _get_json(url: str, token: str = "", timeout_sec: float = 20.0) -> dict[str, object]:
    headers: dict[str, str] = {"User-Agent": "PhoenixGuardFeedVerifier/1.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = request.Request(url, headers=headers, method="GET")
    with request.urlopen(req, timeout=max(1.0, timeout_sec)) as response:
        payload = response.read().decode("utf-8", errors="replace")
    decoded: object = json.loads(payload) if payload.strip() else {}
    if isinstance(decoded, dict):
        return {str(key): value for key, value in cast(Mapping[str, Any], decoded).items()}
    return {"response": decoded}


def _png_bytes() -> bytes:
    image = Image.new("RGB", (320, 180), (13, 18, 24))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _multipart_body(fields: dict[str, str], filename: str, data: bytes) -> tuple[bytes, str]:
    boundary = f"----PhoenixGuardVerify{int(time.time() * 1000)}"
    chunks: list[bytes] = []
    for key, value in fields.items():
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode("ascii"))
        chunks.append(str(value).encode("utf-8"))
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}\r\n".encode("ascii"))
    chunks.append(
        (
            f'Content-Disposition: form-data; name="frame"; filename="{filename}"\r\n'
            "Content-Type: image/png\r\n\r\n"
        ).encode("ascii")
    )
    chunks.append(data)
    chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


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
    canonical = "\n".join(
        [
            "PG_FRAME_INGEST_V1",
            "POST",
            parse.urlparse(endpoint).path,
            session_id,
            source_id,
            sequence_id,
            str(frame_id),
            str(int(capture_epoch_ms or "0")),
            frame_sha256,
            timestamp,
            nonce,
        ]
    )
    signature = hmac.new(signing_secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "X-PhoenixGuard-Signature-Alg": "HMAC-SHA256-V1",
        "X-PhoenixGuard-Timestamp": timestamp,
        "X-PhoenixGuard-Nonce": nonce,
        "X-PhoenixGuard-Signature": f"v1={signature}",
    }


def _post_frame(base_url: str, session_id: str, token: str, frame_id: int, signing_secret: str) -> dict[str, object]:
    source_id = "deployment-verify-source"
    sequence_id = f"deployment-verify-{int(time.time())}"
    capture_epoch_ms = str(int(time.time() * 1000))
    frame_bytes = _png_bytes()
    fields = {
        "source_id": source_id,
        "source_url": "deployment://verify",
        "symbol": "VERIFY",
        "timeframe": "M5",
        "sequence_id": sequence_id,
        "capture_epoch_ms": capture_epoch_ms,
        "frame_id": str(frame_id),
        "metadata_json": '{"source_type":"deployment_verify"}',
    }
    body, content_type = _multipart_body(fields, "deployment_verify.png", frame_bytes)
    endpoint = f"{base_url.rstrip('/')}/v1/mobile/frame-ingest/sessions/{session_id}/frames"
    req = request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": content_type,
            "User-Agent": "PhoenixGuardFeedVerifier/1.0",
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
    with request.urlopen(req, timeout=60.0) as response:
        payload = response.read().decode("utf-8", errors="replace")
    decoded: object = json.loads(payload) if payload.strip() else {}
    if isinstance(decoded, dict):
        return {str(key): value for key, value in cast(Mapping[str, Any], decoded).items()}
    return {"response": decoded}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify PhoenixGuard universal frame-ingest deployment readiness.")
    parser.add_argument("--base-url", required=True, help="PhoenixGuard API base URL.")
    parser.add_argument("--token", default="", help="Optional frame-ingest token for status/upload checks.")
    parser.add_argument("--signing-secret", default="", help="Optional HMAC signing secret for upload smoke checks.")
    parser.add_argument("--session-id", default="deployment-verify", help="Session id for optional upload smoke.")
    parser.add_argument("--upload-smoke", action="store_true", help="Upload one synthetic frame.")
    args = parser.parse_args()

    base_url = str(args.base_url).rstrip("/")
    checks: list[tuple[str, bool, object]] = []
    try:
        health = _get_json(f"{base_url}/v1/mobile/health")
        checks.append(("api_health", health.get("status") == "ok", health))
    except (OSError, TimeoutError, error.URLError, error.HTTPError, json.JSONDecodeError) as exc:
        checks.append(("api_health", False, str(exc)))
    try:
        readiness = _get_json(f"{base_url}/v1/mobile/frame-ingest/readiness")
        checks.append(("frame_ingest_readiness", bool(readiness.get("armed")), readiness))
    except error.HTTPError as exc:
        checks.append(("frame_ingest_readiness", False, exc.read().decode("utf-8", errors="replace")))
    except (OSError, TimeoutError, error.URLError, json.JSONDecodeError) as exc:
        checks.append(("frame_ingest_readiness", False, str(exc)))
    if args.upload_smoke:
        if not str(args.token or "").strip():
            checks.append(("upload_smoke", False, "missing token"))
        else:
            try:
                upload = _post_frame(base_url, str(args.session_id), str(args.token), 1, str(args.signing_secret or ""))
                frame_ingest = upload.get("frame_ingest")
                accepted = True
                if isinstance(frame_ingest, Mapping):
                    frame_ingest_payload = cast(Mapping[str, object], frame_ingest)
                    accepted = bool(frame_ingest_payload.get("accepted", True))
                checks.append(("upload_smoke", accepted, upload))
            except error.HTTPError as exc:
                checks.append(("upload_smoke", False, exc.read().decode("utf-8", errors="replace")))
            except (OSError, TimeoutError, error.URLError, json.JSONDecodeError) as exc:
                checks.append(("upload_smoke", False, str(exc)))
    ok = all(passed for _, passed, _ in checks)
    print(json.dumps({"ok": ok, "checks": [{"name": name, "passed": passed, "detail": detail} for name, passed, detail in checks]}, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
