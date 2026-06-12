from __future__ import annotations

import argparse
import base64
import copy
from collections import OrderedDict
import hashlib
import io
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Lock
from typing import Any, cast

from PIL import Image
import torch

from phoenixguard.core.config import MEMORY_BANK as MEMORY_BANK_CFG, RUNTIME
from phoenixguard.core.utils import setup_logger
from phoenixguard.runtime.local_ensemble_runtime import (
    LegacyFallbackApprovalRequired,
    LocalCVEnsembleRuntime,
)
from phoenixguard.runtime.observability_v3 import collect_compute_usage


logger = setup_logger(RUNTIME.logs_dir / "model_council_daemon.log")
_runtime_cache: OrderedDict[str, LocalCVEnsembleRuntime] = OrderedDict()
_prediction_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_cache_stats: dict[str, int] = {"hits": 0, "misses": 0, "rejects": 0}
_cache_lock = Lock()
_runtime_lock = Lock()
_stats_lock = Lock()


def _bump_cache_stat(key: str) -> None:
    with _stats_lock:
        _cache_stats[key] = int(_cache_stats.get(key, 0)) + 1


def _cache_stats_snapshot() -> dict[str, int]:
    with _stats_lock:
        return dict(_cache_stats)


def _memory_image_dirs() -> list[str]:
    return [
        str(RUNTIME.project_root / MEMORY_BANK_CFG.buys_dir),
        str(RUNTIME.project_root / MEMORY_BANK_CFG.sells_dir),
    ]


def _runtime_status() -> dict[str, Any]:
    loaded_model_names: list[str] = []
    available_model_names: list[str] = []
    failed_models: dict[str, str] = {}
    runtime_profiles: list[dict[str, Any]] = []
    process = collect_compute_usage(include_gpu=True)
    for key, runtime in list(_runtime_cache.items()):
        runtime_profiles.append(
            {
                "key": key,
                "target_models": list(getattr(runtime, "requested_models", [])),
                "max_loaded_models": int(getattr(runtime, "max_loaded_models", 0) or 0),
                "loaded_models": sorted(list(getattr(runtime, "_loaded_runtimes", {}).keys())),
                "process": process.get("process", {}),
            }
        )
        if not loaded_model_names:
            loaded_model_names = sorted(list(getattr(runtime, "_loaded_runtimes", {}).keys()))
            available_model_names = list(getattr(runtime, "loaded_model_names", []))
            failed_models = dict(getattr(runtime, "failed_models", {}))
    stats = _cache_stats_snapshot()
    cache_total = int(stats.get("hits", 0)) + int(stats.get("misses", 0))
    cache_payload = {
        "entries": len(_prediction_cache),
        "hits": int(stats.get("hits", 0)),
        "misses": int(stats.get("misses", 0)),
        "rejects": int(stats.get("rejects", 0)),
        "hit_rate": round(float(stats.get("hits", 0)) / float(cache_total), 4) if cache_total > 0 else 0.0,
    }
    return {
        "running": True,
        "runtime_loaded": bool(_runtime_cache),
        "device_preference": RUNTIME.device_preference,
        "torch_cuda_available": bool(torch.cuda.is_available()),
        "available_models": available_model_names,
        "loaded_models": loaded_model_names,
        "failed_models": failed_models,
        "cache_entries": len(_prediction_cache),
        "cache": cache_payload,
        "cache_hits": cache_payload["hits"],
        "cache_misses": cache_payload["misses"],
        "cache_rejects": cache_payload["rejects"],
        "runtime_profiles": runtime_profiles,
        "process": process,
    }


def _runtime_key(
    target_models: list[str] | None,
    max_loaded_models: int | None,
) -> str:
    model_key = ",".join(str(name).strip().lower() for name in (target_models or []))
    limit_key = "default" if max_loaded_models is None else str(max(1, int(max_loaded_models)))
    return f"{str(RUNTIME.device_preference).strip().lower()}|{model_key or 'profile'}|{limit_key}"


def _get_runtime(
    *,
    target_models: list[str] | None = None,
    max_loaded_models: int | None = None,
) -> LocalCVEnsembleRuntime:
    resolved_targets = list(target_models) if target_models else None
    if (
        resolved_targets is None
        and bool(getattr(RUNTIME, "force_full_council_on_cpu", False))
        and str(RUNTIME.device_preference) == "cpu"
        and not str(os.getenv("PHOENIXGUARD_LOCAL_ENSEMBLE_MODELS", "") or "").strip()
    ):
        resolved_targets = list(LocalCVEnsembleRuntime.DEFAULT_MODELS)
    cache_key = _runtime_key(resolved_targets, max_loaded_models)
    with _runtime_lock:
        cached = _runtime_cache.get(cache_key)
        if cached is not None:
            _runtime_cache.move_to_end(cache_key)
            return cached
        logger.info("Starting model council runtime on %s with profile %s", RUNTIME.device_preference, cache_key)
        runtime = LocalCVEnsembleRuntime(
            image_dirs=_memory_image_dirs(),
            model_dir=RUNTIME.models_dir,
            compute_device=torch.device(RUNTIME.device_preference),
            logger=logger,
            target_models=resolved_targets,
        )
        if max_loaded_models is not None:
            runtime.max_loaded_models = max(1, int(max_loaded_models))
        _runtime_cache[cache_key] = runtime
        _runtime_cache.move_to_end(cache_key)
        while len(_runtime_cache) > 4:
            _runtime_cache.popitem(last=False)
        return runtime


def _image_payload(payload: dict[str, Any]) -> tuple[Image.Image, bytes]:
    image_b64 = str(payload.get("image_b64", "") or "").strip()
    if not image_b64:
        raise ValueError("image_b64 is required")
    raw = base64.b64decode(image_b64.encode("utf-8"))
    return Image.open(io.BytesIO(raw)).convert("RGB"), raw


def _prediction_cache_key(
    raw_image: bytes,
    adaptation_profile: dict[str, Any],
    routing_context: dict[str, Any],
    target_models: list[str] | None,
    max_loaded_models: int | None,
) -> str:
    profile_blob = json.dumps(dict(adaptation_profile), sort_keys=True, ensure_ascii=True).encode("utf-8")
    routing_blob = json.dumps(dict(routing_context), sort_keys=True, ensure_ascii=True).encode("utf-8")
    runtime_blob = json.dumps(
        {
            "target_models": list(target_models or []),
            "max_loaded_models": int(max_loaded_models) if isinstance(max_loaded_models, int) else None,
        },
        sort_keys=True,
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(raw_image + b"::" + profile_blob + b"::" + routing_blob + b"::" + runtime_blob).hexdigest()


def _get_cached_prediction(cache_key: str) -> dict[str, Any] | None:
    with _cache_lock:
        cached = _prediction_cache.get(cache_key)
        if cached is None:
            return None
        _prediction_cache.move_to_end(cache_key)
        return copy.deepcopy(cached)


def _store_cached_prediction(cache_key: str, prediction: dict[str, Any]) -> None:
    cache_limit = max(1, int(getattr(RUNTIME, "model_council_cache_size", 24) or 24))
    with _cache_lock:
        _prediction_cache[cache_key] = copy.deepcopy(prediction)
        _prediction_cache.move_to_end(cache_key)
        while len(_prediction_cache) > cache_limit:
            _prediction_cache.popitem(last=False)


class _ModelCouncilHandler(BaseHTTPRequestHandler):
    server_version = "PhoenixGuardModelCouncil/1.0"

    def log_message(self, format: str, *args: Any) -> None:
        logger.info("daemon_http " + format, *args)

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/status":
            self._send_json(200, _runtime_status())
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        if self.path.rstrip("/") != "/predict":
            self._send_json(404, {"error": "not_found"})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            raw = self.rfile.read(content_length)
            payload_obj = json.loads(raw.decode("utf-8")) if raw else {}
            payload = dict(payload_obj) if isinstance(payload_obj, dict) else {}
            adaptation_profile = cast(dict[str, Any], payload.get("adaptation_profile", {}))
            routing_context = cast(dict[str, Any], payload.get("routing_context", {}))
            raw_target_models = cast(list[Any], payload.get("target_models", []))
            target_models = [str(name).strip() for name in raw_target_models if str(name).strip()] or None
            max_loaded_raw = payload.get("max_loaded_models")
            max_loaded_models = int(max_loaded_raw) if isinstance(max_loaded_raw, int) else None
            image, raw_image = _image_payload(payload)
            cache_key = _prediction_cache_key(
                raw_image,
                adaptation_profile,
                routing_context,
                target_models,
                max_loaded_models,
            )
            cached_prediction = _get_cached_prediction(cache_key)
            if cached_prediction is not None:
                _bump_cache_stat("hits")
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "prediction": cached_prediction,
                        "cached": True,
                        "status": _runtime_status(),
                    },
                )
                return
            _bump_cache_stat("misses")
            runtime = _get_runtime(
                target_models=target_models,
                max_loaded_models=max_loaded_models,
            )
            prediction = runtime.predict(
                image,
                adaptation_profile=adaptation_profile,
                routing_context=routing_context,
            )
            _store_cached_prediction(cache_key, prediction)
            self._send_json(
                200,
                {
                    "ok": True,
                    "prediction": prediction,
                    "cached": False,
                    "status": _runtime_status(),
                },
            )
        except LegacyFallbackApprovalRequired as exc:
            _bump_cache_stat("rejects")
            self._send_json(
                409,
                {
                    "ok": False,
                    "error": str(exc),
                    "legacy_fallback_request": exc.to_payload(),
                },
            )
        except Exception as exc:
            _bump_cache_stat("rejects")
            logger.exception("Model council daemon predict failed: %s", exc)
            self._send_json(500, {"ok": False, "error": str(exc)})


def main() -> None:
    parser = argparse.ArgumentParser(description="PhoenixGuard model council daemon")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8767)
    args = parser.parse_args()

    host = str(args.host)
    port = int(args.port)
    logger.info("Model council daemon listening on %s:%d", host, port)
    server = ThreadingHTTPServer((host, port), _ModelCouncilHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Model council daemon interrupted; shutting down.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
