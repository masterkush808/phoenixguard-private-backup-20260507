from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import cast

import uvicorn

from phoenixguard.runtime.singleton_guard_v3 import guard_from_environment


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
    policy_factory = cast(Callable[[], object] | None, getattr(asyncio, "WindowsSelectorEventLoopPolicy", None))
    policy_setter = cast(Callable[[object], None] | None, getattr(asyncio, "set_event_loop_policy", None))
    if policy_factory is None or policy_setter is None:
        return
    policy_setter(policy_factory())


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
    session_id = str(os.getenv("PHOENIXGUARD_TRACKER_SESSION_ID", "pocket-live-8788") or "pocket-live-8788").strip()
    base_url = f"http://{host}:{port}"
    guard = guard_from_environment(Path(__file__).resolve().parent)
    runtime_token = str(os.getenv("PHOENIXGUARD_RUNTIME_LOCK_TOKEN", "") or "").strip()
    acquired_token = ""
    if runtime_token:
        guard.register_component("api", pid=os.getpid(), session_id=session_id, base_url=base_url, owner_token=runtime_token)
    elif str(os.getenv("PHOENIXGUARD_RUNTIME_SINGLETON_DISABLE", "") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        result = guard.acquire(
            session_id=session_id,
            base_url=base_url,
            data_dir=os.getenv("PHOENIXGUARD_DATA_DIR", ".codex_runtime/data_live"),
            api_port=port,
            launcher_pid=os.getpid(),
            api_pid=os.getpid(),
            takeover_stale=True,
        )
        if not result.ok:
            raise SystemExit(f"PhoenixGuard runtime singleton refused API launch: {result.reason} {result.lock_path}")
        acquired_token = result.owner_token
    try:
        uvicorn.run("phoenixguard.mobile_api.app:create_app", factory=True, host=host, port=port, reload=False)
    finally:
        if acquired_token:
            guard.release(owner_token=acquired_token)
