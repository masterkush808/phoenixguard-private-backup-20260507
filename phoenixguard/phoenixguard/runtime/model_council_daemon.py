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
from phoenixguard.runtime.local_ensemble_runtime import LocalCVEnsembleRuntime


logger = setup_logger(RUNTIME.logs_dir / "model_council_daemon.log")
_runtime: LocalCVEnsembleRuntime | None = None
_prediction_cache: OrderedDict[str, dict[str, Any]] = OrderedDict()
_cache_lock = Lock()


def _memory_image_dirs() -> list[str]:
    return [
        str(RUNTIME.project_root / MEMORY_BANK_CFG.buys_dir),
        str(RUNTIME.project_root / MEMORY_BANK_CFG.sells_dir),
    ]


def _runtime_status() -> dict[str, Any]:
    loaded_model_names: list[str] = []
    available_model_names: list[str] = []
    failed_models: dict[str, str] = {}
    if _runtime is not None:
        loaded_model_names = sorted(list(getattr(_runtime, "_loaded_runtimes", {}).keys()))
        available_model_names = list(getattr(_runtime, "loaded_model_names", []))
        failed_models = dict(getattr(_runtime, "failed_models", {}))
    return {
        "running": True,
        "runtime_loaded": bool(_runtime is not None),
        "device_preference": RUNTIME.device_preference,
        "torch_cuda_available": bool(torch.cuda.is_available()),
        "available_models": available_model_names,
        "loaded_models": loaded_model_names,
        "failed_models": failed_models,
        "cache_entries": len(_prediction_cache),
    }


def _get_runtime() -> LocalCVEnsembleRuntime:
    global _runtime
    if _runtime is None:
        logger.info("Starting model council runtime on %s", RUNTIME.device_preference)
        target_models = None
        if (
            bool(getattr(RUNTIME, "force_full_council_on_cpu", False))
            and str(RUNTIME.device_preference) == "cpu"
            and not str(os.getenv("PHOENIXGUARD_LOCAL_ENSEMBLE_MODELS", "") or "").strip()
        ):
            target_models = list(LocalCVEnsembleRuntime.DEFAULT_MODELS)
        _runtime = LocalCVEnsembleRuntime(
            image_dirs=_memory_image_dirs(),
            model_dir=RUNTIME.models_dir,
            compute_device=torch.device(RUNTIME.device_preference),
            logger=logger,
            target_models=target_models,
        )
    return _runtime


def _image_payload(payload: dict[str, Any]) -> tuple[Image.Image, bytes]:
    image_b64 = str(payload.get("image_b64", "") or "").strip()
    if not image_b64:
        raise ValueError("image_b64 is required")
    raw = base64.b64decode(image_b64.encode("utf-8"))
    return Image.open(io.BytesIO(raw)).convert("RGB"), raw


def _prediction_cache_key(raw_image: bytes, adaptation_profile: dict[str, Any]) -> str:
    profile_blob = json.dumps(dict(adaptation_profile), sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(raw_image + b"::" + profile_blob).hexdigest()


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
            image, raw_image = _image_payload(payload)
            cache_key = _prediction_cache_key(raw_image, adaptation_profile)
            cached_prediction = _get_cached_prediction(cache_key)
            if cached_prediction is not None:
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
            runtime = _get_runtime()
            prediction = runtime.predict(image, adaptation_profile=adaptation_profile)
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
        except Exception as exc:
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
