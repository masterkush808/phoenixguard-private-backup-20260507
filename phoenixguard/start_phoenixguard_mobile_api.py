from __future__ import annotations

import asyncio
import os
import logging
import sys
from pathlib import Path

import uvicorn


def _local_tracing_disabled() -> bool:
    enabled = str(os.getenv("PHOENIXGUARD_ENABLE_OTEL", "") or "").strip().lower()
    return enabled not in {"1", "true", "yes", "on"}


def _disable_local_tracing_export() -> None:
    if not _local_tracing_disabled():
        return
    os.environ["OTEL_SDK_DISABLED"] = "true"
    os.environ["OTEL_TRACES_EXPORTER"] = "none"
    os.environ["OTEL_METRICS_EXPORTER"] = "none"
    os.environ["OTEL_LOGS_EXPORTER"] = "none"
    os.environ["PHOENIXGUARD_TRACING_DISABLED"] = "true"


def _configure_windows_server_loop() -> None:
    if not sys.platform.startswith("win"):
        return
    policy_factory = getattr(asyncio, "WindowsSelectorEventLoopPolicy", None)
    if policy_factory is None:
        return
    asyncio.set_event_loop_policy(policy_factory())


if __name__ == "__main__":
    _disable_local_tracing_export()
    _configure_windows_server_loop()
    log_handlers: list[logging.Handler] = [logging.StreamHandler()]
    logs_dir = str(os.getenv("PHOENIXGUARD_LOGS_DIR", "") or "").strip()
    if logs_dir:
        Path(logs_dir).mkdir(parents=True, exist_ok=True)
        log_handlers.append(logging.FileHandler(Path(logs_dir) / "mobile_api.log", encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=log_handlers,
        force=True,
    )
    host = str(os.getenv("PHOENIXGUARD_MOBILE_API_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
    port = int(os.getenv("PHOENIXGUARD_MOBILE_API_PORT", "8787") or "8787")
    uvicorn.run("phoenixguard.mobile_api.app:create_app", factory=True, host=host, port=port, reload=False)
