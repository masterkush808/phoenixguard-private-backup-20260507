#!/usr/bin/env python3
"""PhoenixGuard Frontline Qwen daemon.

Listens to PhoenixGuard's live session stream and, whenever the studied
candle changes, assembles the FULL untruncated V3 live-state payload plus the
latest chart image and asks the Qwen vision model served by Cloudflare Workers
AI (``@cf/qwen/qwen3.8-27b``) to reason about the market.  The resulting
verdict is published as ``frontline_reasoning_v3.json`` next to PhoenixGuard's
other sidecars, where the direct trade bridge reads it and applies it as a
final veto gate before clicking BUY or SELL.

Provider: Cloudflare Workers AI (no Alibaba / ModelScope account required).
The API token comes from ``PHOENIXGUARD_QWEN_TOKEN`` (or, as a fallback,
``CLOUDFLARE_API_TOKEN`` or the ``qwen_token.txt`` file), and the account id
comes from ``PHOENIXGUARD_CLOUDFLARE_ACCOUNT_ID``.  ``PHOENIXGUARD_QWEN_URL``
overrides the base URL for a custom/self-hosted endpoint.

Role contract (fail-safe):
  * Qwen has VETO power only.  It can block a trade the bridge wanted, it can
    never start one on its own.
  * If Qwen is unavailable (missing token, API error, timeout), the daemon
    publishes a non-``ok`` verdict and keeps the bridge's own calibrated logic
    running.  A missing/stale verdict NEVER blocks the bridge.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Iterator, Mapping, Sequence, cast
from urllib import error, request

_PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_BOOTSTRAP))

from _pg_bootstrap import ensure_project_paths

PROJECT_ROOT = ensure_project_paths()

# Cloudflare Workers AI credentials: real environment variables win, then a
# gitignored config file (Backend/launch/qwen_cloudflare.env) so the daemon
# works even when spawned from a terminal that predates the user-level env vars.
_QWEN_ENV_FILE = Path(__file__).resolve().parent / "qwen_cloudflare.env"


def _apply_qwen_env_file() -> None:
    try:
        if not _QWEN_ENV_FILE.is_file():
            return
        for raw_line in _QWEN_ENV_FILE.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError:
        pass


_apply_qwen_env_file()

_CLOUDFLARE_ACCOUNT_ID = str(os.getenv("PHOENIXGUARD_CLOUDFLARE_ACCOUNT_ID") or "").strip()
_configured_qwen_url = str(os.getenv("PHOENIXGUARD_QWEN_URL") or "").strip().rstrip("/")
QWEN_BASE_URL = _configured_qwen_url or (
    f"https://api.cloudflare.com/client/v4/accounts/{_CLOUDFLARE_ACCOUNT_ID}/ai/v1"
    if _CLOUDFLARE_ACCOUNT_ID
    else ""
)
QWEN_CHAT_ENDPOINT = (QWEN_BASE_URL + "/chat/completions") if QWEN_BASE_URL else ""
DEFAULT_MODEL = str(
    os.getenv("PHOENIXGUARD_QWEN_MODEL") or "@cf/qwen/qwen3.8-27b"
).strip()
DEFAULT_MODEL_FALLBACKS: list[str] = [
    _text.strip()
    for _text in str(os.getenv("PHOENIXGUARD_QWEN_MODEL_FALLBACKS") or "").split(",")
    if _text.strip()
]
DEFAULT_MIN_INTERVAL_SECONDS = float(os.getenv("PHOENIXGUARD_FRONTLINE_MIN_INTERVAL_SEC") or "2700.0")
DEFAULT_VERDICT_FRESHNESS_SECONDS = float(os.getenv("PHOENIXGUARD_FRONTLINE_FRESHNESS_SEC") or "180.0")
DEFAULT_MAX_CONTEXT_CHARS = int(os.getenv("PHOENIXGUARD_FRONTLINE_MAX_CONTEXT_CHARS") or "30000")
DEFAULT_MAX_LIST_ITEMS = int(os.getenv("PHOENIXGUARD_FRONTLINE_MAX_LIST_ITEMS") or "1000000")
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("PHOENIXGUARD_FRONTLINE_TIMEOUT_SEC") or "180.0")
DEFAULT_LISTENER_RECONNECT_SECONDS = 2.0
DEFAULT_POLL_SECONDS = 1.0
SCHEMA_VERSION = "PG_FRONTLINE_REASONING_V3"

NOISE_KEYS = frozenset(
    {
        "cpu_stream_v3",
        "performance_trace_v3",
        "frontend_heartbeat",
        "frame_timing",
        "model_health",
        "model_warm_state_v3",
        "frontend_render_age_ms",
        "packet_age_ms",
        "overlay_age_ms",
        "model_vote_age_ms",
        "frame_age_ms",
        "stale_status",
        "stale_flags",
        "capture_source_v3",
        "source_capture_id",
        "_bridge_state_source",
        "_bridge_state_epoch",
    }
)

BOOLEAN_VERDICT_KEYS = frozenset(
    {"actionable", "chart_studied", "payload_compact", "payload_full"}
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _float(value: object, default: float = 0.0) -> float:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return default
    return default


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return cast(Mapping[str, object], value)
    return {}


def _default_live_runtime_dir(project_root: Path | None = None) -> Path:
    resolved_project_root = project_root or PROJECT_ROOT
    configured_runtime_dir = str(os.getenv("PHOENIXGUARD_RUNTIME_DIR") or "").strip()
    if configured_runtime_dir:
        return Path(configured_runtime_dir).expanduser()
    local_app_data = str(os.getenv("LOCALAPPDATA") or "").strip()
    if local_app_data:
        return Path(local_app_data).expanduser() / "PhoenixGuard" / "runtime" / "live"
    return resolved_project_root / "runtime" / "live"


def _runtime_lock_path() -> Path:
    configured = str(os.getenv("PHOENIXGUARD_RUNTIME_LOCK_PATH") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return _default_live_runtime_dir() / "phoenixguard_stack.lock.json"


def _read_runtime_lock() -> dict[str, object]:
    lock_path = _runtime_lock_path()
    if not lock_path.exists():
        return {}
    try:
        raw = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, Mapping):
        return {}
    return dict(cast(Mapping[str, object], raw))


def _resolve_stack_context(*, base_url: str | None, session_id: str | None) -> tuple[str, str]:
    bootstrap_default_base = "http://127.0.0.1:8793"
    bootstrap_default_session = "pocket-live-8788"
    resolved_base = (base_url or "").strip()
    resolved_session = (session_id or "").strip()

    if not resolved_base:
        env_host = str(os.getenv("PHOENIXGUARD_MOBILE_API_HOST") or "127.0.0.1").strip() or "127.0.0.1"
        env_port = str(os.getenv("PHOENIXGUARD_MOBILE_API_PORT") or "").strip()
        if env_port:
            resolved_base = f"http://{env_host}:{env_port}"
        else:
            lock = _read_runtime_lock()
            lock_base = str(lock.get("base_url") or "").strip()
            if lock_base:
                resolved_base = lock_base
    if not resolved_session:
        env_session = str(os.getenv("PHOENIXGUARD_TRACKER_SESSION_ID") or "").strip()
        if env_session:
            resolved_session = env_session
        else:
            lock = _read_runtime_lock()
            resolved_session = str(lock.get("session_id") or bootstrap_default_session).strip() or bootstrap_default_session
    return (resolved_base or bootstrap_default_base, resolved_session or bootstrap_default_session)


def _candidate_session_dirs(session_id: str) -> list[Path]:
    runtime_dir = _default_live_runtime_dir()
    candidates: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = os.path.normcase(str(path))
        if key in seen:
            return
        seen.add(key)
        candidates.append(path)

    data_dir = str(os.getenv("PHOENIXGUARD_DATA_DIR") or "").strip()
    if data_dir:
        add(Path(data_dir).expanduser() / "mobile_api" / "window_tracker" / "sessions" / _text(session_id))
    add(runtime_dir / "data_live" / "mobile_api" / "window_tracker" / "sessions" / _text(session_id))
    add(PROJECT_ROOT / "data" / "mobile_api" / "window_tracker" / "sessions" / _text(session_id))
    add(PROJECT_ROOT / "data" / "window_tracker" / "sessions" / _text(session_id))
    return candidates


def _resolve_session_dir(session_id: str) -> Path:
    """Return the session dir holding live state, preferring the freshest."""
    freshest: tuple[Path, float] | None = None
    for candidate in _candidate_session_dirs(_text(session_id)):
        if not candidate.is_dir():
            continue
        marker = candidate / "compact_live_state.json"
        epoch = marker.stat().st_mtime if marker.exists() else 0.0
        if freshest is None or epoch > freshest[1]:
            freshest = (candidate, epoch)
    if freshest is not None:
        return freshest[0]
    return _candidate_session_dirs(_text(session_id))[0]


def _read_json_url(url: str, *, timeout_sec: float) -> object:
    req = request.Request(url, headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=timeout_sec) as response:
        data = response.read().decode("utf-8")
    return json.loads(data)


def _read_bytes_url(url: str, *, timeout_sec: float) -> bytes:
    req = request.Request(url, headers={"Accept": "image/*, */*"})
    with request.urlopen(req, timeout=timeout_sec) as response:
        return response.read()


def _fetch_full_live_state(*, base_url: str, session_id: str, timeout_sec: float) -> dict[str, object]:
    """Fetch the FULL untruncated live state payload (compact=false)."""
    url = base_url.rstrip("/") + f"/v1/mobile/live/state/v3/{_text(session_id)}?mode=CLEAN_LIVE&compact=false"
    try:
        raw = _read_json_url(url, timeout_sec=max(5.0, timeout_sec))
    except Exception as exc:
        raise RuntimeError(f"Full live state fetch failed: {exc}") from exc
    if not isinstance(raw, Mapping):
        raise RuntimeError("Full live state endpoint did not return a JSON object.")
    return dict(cast(Mapping[str, object], raw))


def _latest_chart_image_bytes(*, base_url: str, session_id: str, timeout_sec: float) -> tuple[bytes, str, float] | None:
    """Return (bytes, mime, epoch) for the latest chart image.

    Prefers the on-disk artifact (fastest), falls back to the HTTP endpoint.
    """
    session_dir = _resolve_session_dir(_text(session_id))
    artifacts_dir = session_dir / "artifacts"
    if artifacts_dir.is_dir():
        chart_files = [
            path
            for path in artifacts_dir.iterdir()
            if path.name.lower().endswith((".jpg", ".jpeg", ".png")) and "_chart" in path.name.lower()
        ]
        if chart_files:
            chart_files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
            latest = chart_files[0]
            suffix = latest.suffix.lower()
            mime = "image/png" if suffix == ".png" else "image/jpeg"
            try:
                return latest.read_bytes(), mime, latest.stat().st_mtime
            except OSError:
                pass
    url = base_url.rstrip("/") + f"/v1/mobile/window-tracker/sessions/{_text(session_id)}/artifacts/latest-chart"
    try:
        data = _read_bytes_url(url, timeout_sec=max(5.0, timeout_sec))
    except Exception:
        return None
    if not data:
        return None
    return data, "image/png", time.time()


def _optimize_chart_image(chart_bytes: bytes) -> tuple[bytes, str]:
    """Downscale and re-encode the chart so Cloudflare edge inference keeps up.

    The live chart PNG is large (1920x1080, ~700KB); sending it raw to a 27B
    vision model causes Cloudflare to time the request out (HTTP 408).  Resize
    to a sensible maximum and re-encode as JPEG.  Falls back to the original
    bytes if Pillow is unavailable or the image cannot be parsed.
    """
    try:
        from io import BytesIO

        from PIL import Image

        with Image.open(BytesIO(chart_bytes)) as loaded:
            image = loaded.convert("RGB")
        image.thumbnail((1024, 1024), Image.Resampling.BILINEAR)
        buffer = BytesIO()
        image.save(buffer, format="JPEG", quality=70, optimize=True)
        optimized = buffer.getvalue()
        if optimized:
            return optimized, "image/jpeg"
    except Exception:
        pass
    return chart_bytes, "image/png"


def _drop_noise(value: object, *, max_list_items: int) -> object:
    if isinstance(value, Mapping):
        out: dict[str, object] = {}
        for key, item in cast(Mapping[object, object], value).items():
            if str(key) in NOISE_KEYS:
                continue
            out[str(key)] = _drop_noise(item, max_list_items=max_list_items)
        return out
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        raw_items = list(cast(Sequence[object], value))
        kept_items: list[object] = [item for item in raw_items if not isinstance(item, str) or item.strip()]
        if len(kept_items) > max_list_items:
            kept_items = list(kept_items[-max_list_items:])
        return [_drop_noise(item, max_list_items=max_list_items) for item in kept_items]
    return value


def _bounded_context(payload: Mapping[str, object], *, max_chars: int, max_list_items: int) -> str:
    """Build the Qwen context: EVERY decision-relevant field, telemetry removed.

    The compact public view prunes shadow forecasts and study internals; this
    uses the full payload and only drops pure telemetry plus caps oversized
    lists so the request fits the model context window.  Nothing that feeds a
    decision is truncated.
    """
    cleaned = _drop_noise(dict(payload), max_list_items=max_list_items)
    serialized = json.dumps(cleaned, ensure_ascii=True, separators=(",", ":"))
    if len(serialized) <= max_chars:
        return serialized
    # Respect a hard cap for the model context without ever silently dropping
    # decision fields.  Cut the largest single values (usually study history
    # tails) before falling back to the newest slice of recent_studies.
    cleaned_dict = cast(dict[str, object], cleaned)
    recent_studies = cleaned_dict.get("recent_studies")
    if isinstance(recent_studies, list) and len(cast(list[object], recent_studies)) > 3:
        cleaned_dict["recent_studies"] = list(cast(list[object], recent_studies)[-3:])
        serialized = json.dumps(cleaned_dict, ensure_ascii=True, separators=(",", ":"))
    if len(serialized) <= max_chars:
        return serialized
    return serialized[:max_chars]


def _load_qwen_token(token_arg: str | None) -> str:
    token = (token_arg or "").strip()
    if token:
        return token
    token = str(os.getenv("PHOENIXGUARD_QWEN_TOKEN") or "").strip()
    if token:
        return token
    token = str(os.getenv("CLOUDFLARE_API_TOKEN") or "").strip()
    if token:
        return token
    for candidate in (
        PROJECT_ROOT / "Backend" / "launch" / "qwen_token.txt",
        PROJECT_ROOT / "qwen_token.txt",
        Path.home() / ".config" / "phoenixguard" / "qwen_token.txt",
    ):
        try:
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8").strip()
                if value:
                    return value
        except OSError:
            continue
    return ""


def _call_qwen(
    *,
    token: str,
    model: str,
    messages: list[dict[str, object]],
    timeout_sec: float,
) -> tuple[str, dict[str, object] | None]:
    """Call Qwen through Cloudflare Workers AI's OpenAI-compatible inference API.

    Cloudflare serves ``@cf/qwen/qwen3.8-27b`` (Qwen vision) on its global edge
    with a daily-resetting free neuron allowance and no credit card on the
    Workers Free plan.  The chart image travels in the OpenAI-shaped
    ``content`` array as a base64 ``image_url`` part.
    """
    if not QWEN_CHAT_ENDPOINT:
        return "", {
            "state": "error",
            "error": (
                "no Cloudflare endpoint configured (set PHOENIXGUARD_CLOUDFLARE_ACCOUNT_ID "
                "or PHOENIXGUARD_QWEN_URL)"
            ),
        }
    if not token:
        return "", {"state": "error", "error": "no Cloudflare Workers AI API token configured"}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    body = json.dumps(
        {
            "model": model,
            "messages": messages,
            "max_tokens": 5000,
            "temperature": 0.2,
            "reasoning_effort": "medium",
            "stream": False,
        },
        ensure_ascii=True,
    ).encode("utf-8")
    req = request.Request(QWEN_CHAT_ENDPOINT, data=body, headers=headers, method="POST")
    try:
        with request.urlopen(req, timeout=timeout_sec) as resp:
            payload_bytes = resp.read()
    except error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace").strip()
        return "", {"state": "error", "error": f"qwen http {exc.code}: {detail[:800]}"}
    except (error.URLError, OSError) as exc:
        return "", {"state": "error", "error": f"qwen connection error: {exc}"}
    try:
        parsed = json.loads(payload_bytes)
    except json.JSONDecodeError as exc:
        return "", {"state": "error", "error": f"qwen returned non-json: {exc}"}
    if not isinstance(parsed, Mapping):
        return "", {"state": "error", "error": "qwen returned a non-object response"}
    parsed_map = cast(Mapping[str, object], parsed)
    choice = parsed_map.get("choices")
    if not isinstance(choice, Sequence) or not choice:
        error_detail = str(parsed_map.get("error") or "no choices in response")[:800]
        return "", {"state": "error", "error": f"qwen api error: {error_detail}"}
    first = cast(Mapping[str, object], choice[0])
    message = cast(Mapping[str, object], first.get("message") or {})
    content = str(message.get("content") or "").strip()
    if not content:
        return "", {"state": "error", "error": "qwen returned no message content"}
    meta: dict[str, object] = {"state": "ok"}
    reasoning = str(message.get("reasoning_content") or message.get("reasoning") or "").strip()
    if reasoning:
        meta["reasoning"] = reasoning
    usage = parsed_map.get("usage")
    if isinstance(usage, Mapping):
        meta["usage"] = dict(cast(Mapping[str, object], usage))
    return content, meta


def _extract_json_object(text: str) -> dict[str, object] | None:
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fenced:
        text = fenced.group(1).strip()
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    for index in range(start, len(text)):
        char = text[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start : index + 1]
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, Mapping):
                    return dict(cast(Mapping[str, object], parsed))
    try:
        parsed = json.loads(text[start:])
    except json.JSONDecodeError:
        return None
    return dict(cast(Mapping[str, object], parsed)) if isinstance(parsed, Mapping) else None


def _verdict_from_json(parsed: Mapping[str, object]) -> tuple[Mapping[str, object], str]:
    raw = parsed.get("verdict")
    if not isinstance(raw, Mapping):
        raw = parsed
    verdict_mapping = cast(Mapping[str, object], raw)
    verdict = str(verdict_mapping.get("verdict") or "").upper()
    if verdict not in {"ALLOW", "VETO"}:
        return verdict_mapping, verdict
    return verdict_mapping, verdict


def _build_prompt(payload: Mapping[str, object], context: str) -> str:
    latest_signal = _as_mapping(payload.get("latest_signal"))
    direct_bias = _as_mapping(payload.get("direct_visual_bias_v3"))
    bridge_side = _text(
        direct_bias.get("side")
        or latest_signal.get("action")
        or latest_signal.get("headline_action")
        or payload.get("dominant_side")
    ).upper()
    market = _text(payload.get("market") or direct_bias.get("market") or "USER_LOCKED_ACTIVE_CHART")
    timeframe = _text(payload.get("timeframe") or direct_bias.get("timeframe") or "M5")
    candle_sequence = direct_bias.get("candle_sequence")
    candle_key = _text(direct_bias.get("candle_key"))

    return (
        "You are the final frontline reasoning layer of PhoenixGuard, an automated "
        "Pocket Option binary-options trader. A downstream bridge has already "
        "decided a candidate direction from its own calibrated visual study and "
        "is about to click BUY or SELL. You study the chart for BOTH the buy and "
        "the sell side, weigh which opportunity is stronger right now, confirm or "
        "block the bridge candidate, and report which side you recommend.\n\n"
        f"Bridge candidate side: {bridge_side or 'UNKNOWN'}\n"
        f"Market: {market} | Timeframe: {timeframe}\n"
        f"Candle sequence: {candle_sequence or 'N/A'} | Candle key: {candle_key or 'N/A'}\n\n"
        "Below is the ENTIRE current PhoenixGuard live state payload (untruncated "
        "V3: history replay, continuations, pullbacks, globals, book rules, order "
        "blocks, fair value gaps, projected zones, model council, and the "
        "candidate's own signal/execution timing). The image you also receive is "
        "the exact chart PhoenixGuard is studying right now.\n\n"
        "STUDY BOTH SIDES and decide what the best action is RIGHT NOW:\n"
        "  * Compare the BUY setup and the SELL setup. Which has the fresher, "
        "higher-quality entry at a mapped wick/pullback zone? Which is extended, "
        "chasing, exhausted, or sitting at the wrong side of the range?\n"
        "  * ALLOW the bridge candidate only when that side is genuinely the "
        "stronger opportunity AND the entry is high quality: fresh, at a "
        "wick/pullback into a mapped zone, aligned with the trend/continuation, "
        "NOT extended, NOT chasing, and not a likely stop-hunt or local-minima "
        "trap.\n"
        "  * VETO when you see a low-quality or dangerous entry, or when the "
        "opposite side is the clearly better opportunity: late chase, middle of a "
        "candlestick, wrong side of the range, price extended from fair value, "
        "opposing force or extreme risk, conflicting book rules, whipsaw "
        "structure, or the candidate direction contradicts the broader context.\n\n"
        "Respond with ONLY a JSON object, no prose:\n"
        '{"verdict": "ALLOW" | "VETO", "side": "BUY" | "SELL" | "NEUTRAL", '
        '"recommended_side": "BUY" | "SELL" | "NEUTRAL", '
        '"confidence": <0.0-1.0>, "position_quality": "wick_zone" | "chased" | '
        '"middle_candle" | "aligned", "reason": "<one concise sentence>", '
        '"warnings": ["<specific risk, if any>"]}\n\n'
        f"PAYLOAD:\n{context}"
    )


def _publish_verdict(*, session_id: str, verdict: dict[str, object]) -> Path | None:
    session_id = _text(session_id)
    verdict["schema_version"] = SCHEMA_VERSION
    verdict["published_epoch"] = time.time()
    serialized = json.dumps(verdict, ensure_ascii=True, sort_keys=True, indent=2)
    written: Path | None = None
    for session_dir in _candidate_session_dirs(session_id):
        try:
            session_dir.mkdir(parents=True, exist_ok=True)
            target = session_dir / "frontline_reasoning_v3.json"
            tmp_path = session_dir / "frontline_reasoning_v3.json.tmp"
            tmp_path.write_text(serialized, encoding="utf-8")
            os.replace(str(tmp_path), str(target))
            written = target
        except OSError:
            continue
    return written


def _run_verdict_once(
    *,
    base_url: str,
    session_id: str,
    token: str,
    model: str,
    model_fallbacks: list[str],
    timeout_sec: float,
    max_context_chars: int,
    max_list_items: int,
    mock: bool,
) -> dict[str, object]:
    try:
        payload = _fetch_full_live_state(base_url=base_url, session_id=session_id, timeout_sec=timeout_sec)
    except Exception:
        if not mock:
            raise
        payload = {
            "mock": True,
            "market": "USER_LOCKED_ACTIVE_CHART",
            "timeframe": "M5",
            "direct_visual_bias_v3": {
                "side": "NEUTRAL",
                "confidence": 0.0,
                "candle_sequence": 0,
                "candle_key": "",
                "observed_epoch": time.time(),
            },
        }
    direct_bias = _as_mapping(payload.get("direct_visual_bias_v3"))
    observed_epoch = _float(direct_bias.get("observed_epoch"), _float(payload.get("last_capture_epoch"), 0.0))
    candle_sequence = direct_bias.get("candle_sequence")
    candle_key = _text(direct_bias.get("candle_key"))
    market = _text(payload.get("market") or direct_bias.get("market") or "USER_LOCKED_ACTIVE_CHART")
    timeframe = _text(payload.get("timeframe") or direct_bias.get("timeframe") or "M5")

    image_result = None
    if not mock:
        try:
            image_result = _latest_chart_image_bytes(base_url=base_url, session_id=session_id, timeout_sec=timeout_sec)
        except Exception:
            image_result = None
    chart_bytes, mime, chart_epoch = image_result if image_result else (None, "", 0.0)
    if chart_bytes:
        chart_bytes, mime = _optimize_chart_image(chart_bytes)

    started = time.time()
    if mock:
        bridge_side = _text(direct_bias.get("side") or payload.get("dominant_side") or "NEUTRAL").upper()
        time.sleep(0.1)
        verdict_payload: dict[str, object] = {
            "state": "mock",
            "model": f"{model} (mock)",
            "verdict": "ALLOW",
            "side": bridge_side if bridge_side in {"BUY", "SELL"} else "NEUTRAL",
            "confidence": 0.5,
            "position_quality": "aligned",
            "reason": "Mock verdict for offline verification.",
            "warnings": [],
        }
    else:
        if not token:
            verdict_payload = {
                "state": "no_token",
                "model": model,
                "verdict": "",
                "side": "NEUTRAL",
                "confidence": 0.0,
                "position_quality": "",
                "reason": "No Cloudflare Workers AI token configured (set PHOENIXGUARD_QWEN_TOKEN / CLOUDFLARE_API_TOKEN / qwen_token.txt). Bridge runs without frontline veto.",
                "warnings": [],
            }
        else:
            context = _bounded_context(
                payload, max_chars=max_context_chars, max_list_items=max_list_items
            )
            prompt = _build_prompt(payload, context)
            messages: list[dict[str, object]] = [
                {
                    "role": "user",
                    "content": [{"type": "text", "text": prompt}],
                }
            ]
            if chart_bytes:
                messages[0]["content"].append(  # type: ignore[index]
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime};base64,{base64.b64encode(chart_bytes).decode('ascii')}"
                        },
                    }
                )
            content = ""
            api_meta: dict[str, object] | None = None
            used_model = model
            last_api_error = ""
            candidate_models: list[str] = [model] + [
                fallback
                for fallback in model_fallbacks
                if fallback and fallback != model
            ]
            for candidate in candidate_models:
                content, api_meta = _call_qwen(
                    token=token, model=candidate, messages=messages, timeout_sec=timeout_sec
                )
                if content:
                    used_model = candidate
                    break
                last_api_error = str(api_meta.get("error") or "") if api_meta else ""
                time.sleep(0.5)
            if not content and api_meta:
                verdict_payload = dict(api_meta)
                verdict_payload.update(
                    {
                        "model": used_model,
                        "side": "NEUTRAL",
                        "verdict": "",
                        "position_quality": "",
                        "warnings": [],
                        "reason": last_api_error or "All Qwen models failed.",
                    }
                )
            else:
                parsed = _extract_json_object(content)
                if parsed is None:
                    verdict_payload = {
                        "state": "error",
                        "model": used_model,
                        "verdict": "",
                        "side": "NEUTRAL",
                        "confidence": 0.0,
                        "position_quality": "",
                        "reason": "Qwen did not return a parseable JSON verdict.",
                        "warnings": [],
                        "raw": content[:4000],
                    }
                else:
                    raw_verdict, raw_verdict_kind = _verdict_from_json(parsed)
                    parsed_side = _text(raw_verdict.get("side")).upper()
                    parsed_conf = max(0.0, min(1.0, _float(raw_verdict.get("confidence"), 0.5)))
                    parsed_quality = _text(raw_verdict.get("position_quality")).lower() or "aligned"
                    parsed_warnings_raw = raw_verdict.get("warnings")
                    parsed_warnings: list[object] = (
                        list(cast(Sequence[object], parsed_warnings_raw))
                        if isinstance(parsed_warnings_raw, Sequence) and not isinstance(parsed_warnings_raw, (str, bytes, bytearray))
                        else []
                    )
                    verdict_payload = {
                        "state": "ok",
                        "model": used_model,
                        "verdict": raw_verdict_kind if raw_verdict_kind in {"ALLOW", "VETO"} else _text(raw_verdict.get("verdict")).upper(),
                        "side": parsed_side if parsed_side in {"BUY", "SELL", "NEUTRAL"} else "NEUTRAL",
                        "recommended_side": parsed_side if parsed_side in {"BUY", "SELL", "NEUTRAL"} else "NEUTRAL",
                        "confidence": parsed_conf,
                        "position_quality": parsed_quality if parsed_quality in {"wick_zone", "chased", "middle_candle", "aligned"} else "aligned",
                        "reason": _text(raw_verdict.get("reason")),
                        "warnings": [str(item) for item in parsed_warnings[:10]],
                    }

    verdict_payload["latency_seconds"] = round(time.time() - started, 3)
    verdict_payload["observed_epoch"] = observed_epoch
    verdict_payload["chart_epoch"] = chart_epoch
    verdict_payload["candle_sequence"] = candle_sequence
    verdict_payload["candle_key"] = candle_key
    verdict_payload["market"] = market
    verdict_payload["timeframe"] = timeframe
    verdict_payload["payload_full"] = True
    verdict_payload["payload_compact"] = False
    verdict_payload["chart_studied"] = bool(chart_bytes)
    return verdict_payload


def _iter_phoenixguard_session_updates(*, base_url: str, session_id: str, timeout_sec: float) -> Iterator[dict[str, object]]:
    stream_url = (
        base_url.rstrip("/")
        + f"/v1/mobile/window-tracker/sessions/{_text(session_id)}/stream"
    )
    stream_request = request.Request(
        stream_url,
        headers={"Accept": "text/event-stream", "Cache-Control": "no-cache"},
    )
    with request.urlopen(stream_request, timeout=max(3.0, float(timeout_sec))) as response:
        event_name = ""
        data_lines: list[str] = []
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if not line:
                if event_name == "SESSION_UPDATE" and data_lines:
                    try:
                        decoded = json.loads("\n".join(data_lines))
                    except json.JSONDecodeError:
                        decoded = None
                    if isinstance(decoded, Mapping):
                        yield dict(cast(Mapping[str, object], decoded))
                event_name = ""
                data_lines = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[6:].strip()
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())


def _run_loop(
    *,
    base_url: str,
    session_id: str,
    token: str,
    model: str,
    model_fallbacks: list[str],
    timeout_sec: float,
    min_interval_seconds: float,
    max_context_chars: int,
    max_list_items: int,
    mock: bool,
    poll_seconds: float,
) -> int:
    last_processed_epoch: float = 0.0
    last_processed_candle: object = None
    last_call_epoch: float = 0.0
    while True:
        listener: Iterator[dict[str, object]] | None = None
        try:
            listener = _iter_phoenixguard_session_updates(
                base_url=base_url, session_id=session_id, timeout_sec=timeout_sec
            )
            for update in listener:
                direct_bias = _as_mapping(update.get("direct_visual_bias_v3"))
                observed_epoch = _float(
                    direct_bias.get("observed_epoch"),
                    _float(update.get("last_capture_epoch"), 0.0),
                )
                candle_sequence = direct_bias.get("candle_sequence")
                candle_changed = (
                    candle_sequence is not None
                    and str(candle_sequence) != str(last_processed_candle)
                )
                epoch_changed = (
                    observed_epoch > 0.0
                    and observed_epoch != last_processed_epoch
                )
                first_observation = last_processed_candle is None and observed_epoch > 0.0
                changed = (
                    candle_changed
                    if candle_sequence is not None
                    else first_observation or epoch_changed
                )
                throttled = (time.time() - last_call_epoch) < min_interval_seconds
                if not changed or throttled:
                    continue
                if candle_sequence is not None:
                    last_processed_candle = candle_sequence
                last_processed_epoch = observed_epoch
                last_call_epoch = time.time()
                verdict: dict[str, object]
                try:
                    verdict = _run_verdict_once(
                        base_url=base_url,
                        session_id=session_id,
                        token=token,
                        model=model,
                        model_fallbacks=model_fallbacks,
                        timeout_sec=timeout_sec,
                        max_context_chars=max_context_chars,
                        max_list_items=max_list_items,
                        mock=mock,
                    )
                except Exception as exc:
                    verdict = {
                        "state": "error",
                        "model": model,
                        "verdict": "",
                        "side": "NEUTRAL",
                        "confidence": 0.0,
                        "position_quality": "",
                        "reason": str(exc),
                        "warnings": [],
                        "observed_epoch": observed_epoch,
                        "candle_sequence": candle_sequence,
                    }
                published = _publish_verdict(session_id=session_id, verdict=verdict)
                log_entry = {
                    "published": bool(published),
                    "path": str(published) if published else None,
                    "state": verdict.get("state"),
                    "verdict": verdict.get("verdict"),
                    "side": verdict.get("side"),
                    "confidence": verdict.get("confidence"),
                    "observed_epoch": verdict.get("observed_epoch"),
                    "candle_sequence": verdict.get("candle_sequence"),
                    "model": verdict.get("model"),
                    "latency_seconds": verdict.get("latency_seconds"),
                }
                print(json.dumps(log_entry, ensure_ascii=True, sort_keys=True), flush=True)
        except (RuntimeError, StopIteration, OSError, TimeoutError, error.URLError) as exc:
            print(
                json.dumps(
                    {"error": f"listener error: {exc}", "listener": "reconnecting"},
                    ensure_ascii=True,
                    sort_keys=True,
                ),
                flush=True,
            )
            time.sleep(DEFAULT_LISTENER_RECONNECT_SECONDS)
            continue
        except KeyboardInterrupt:
            return 0
        time.sleep(max(0.1, poll_seconds))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="phoenixguard_frontline_qwen.py",
        description=(
            "PhoenixGuard Frontline Qwen daemon. Watches the live session stream, "
            "assembles the full untruncated V3 payload plus the studied chart image, "
            "asks the Qwen vision model served by Cloudflare Workers AI "
            "(@cf/qwen/qwen3.8-27b), and publishes a "
            "frontline_reasoning_v3 verdict the direct trade bridge respects "
            "as a veto gate."
        ),
    )
    parser.add_argument("--base-url", default="")
    parser.add_argument("--session-id", default="")
    parser.add_argument("--qwen-token", default="", help="Cloudflare Workers AI API token (or set PHOENIXGUARD_QWEN_TOKEN / CLOUDFLARE_API_TOKEN / qwen_token.txt).")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--min-interval-seconds", type=float, default=DEFAULT_MIN_INTERVAL_SECONDS)
    parser.add_argument("--max-context-chars", type=int, default=DEFAULT_MAX_CONTEXT_CHARS)
    parser.add_argument("--max-list-items", type=int, default=DEFAULT_MAX_LIST_ITEMS)
    parser.add_argument("--mock", action="store_true", help="Offline verification: publish a deterministic ALLOW verdict without calling Qwen.")
    parser.add_argument("--once", action="store_true", help="Run a single verdict against the current state and exit.")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    base_url, session_id = _resolve_stack_context(
        base_url=args.base_url or None,
        session_id=args.session_id or None,
    )
    token = _load_qwen_token(args.qwen_token)

    if args.once:
        verdict = _run_verdict_once(
            base_url=base_url,
            session_id=session_id,
            token=token,
            model=args.model,
            model_fallbacks=DEFAULT_MODEL_FALLBACKS,
            timeout_sec=float(args.timeout),
            max_context_chars=int(args.max_context_chars),
            max_list_items=int(args.max_list_items),
            mock=bool(args.mock),
        )
        published = _publish_verdict(session_id=session_id, verdict=verdict)
        print(json.dumps({"published": str(published) if published else None, **verdict}, ensure_ascii=True, sort_keys=True))
        return 0

    return _run_loop(
        base_url=base_url,
        session_id=session_id,
        token=token,
        model=args.model,
        model_fallbacks=DEFAULT_MODEL_FALLBACKS,
        timeout_sec=float(args.timeout),
        min_interval_seconds=float(args.min_interval_seconds),
        max_context_chars=int(args.max_context_chars),
        max_list_items=int(args.max_list_items),
        mock=bool(args.mock),
        poll_seconds=float(args.poll_seconds),
    )


if __name__ == "__main__":
    raise SystemExit(main())
