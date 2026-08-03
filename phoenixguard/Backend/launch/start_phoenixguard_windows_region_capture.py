from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
sys.dont_write_bytecode = True

_PROJECT_ROOT_BOOTSTRAP = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT_BOOTSTRAP) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT_BOOTSTRAP))

from _pg_bootstrap import ensure_project_paths

PROJECT_ROOT = ensure_project_paths()

import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import logging
import platform
import time
from typing import Any

from phoenixguard.mobile_api.windows_region_capture_v3 import (
    GlobalRegionHotkeyLoopV3,
    PhoenixGuardRegionIngestClientV3,
    WGC_RUNTIME_DISTRIBUTION,
    WGC_RUNTIME_VERSION,
    WindowsRegionCaptureManagerV3,
    require_windows_capture_runtime_v3,
    windows_capture_runtime_version_v3,
)
from phoenixguard.runtime.python_environment_v3 import assert_repo_venv_runtime


LOGGER = logging.getLogger("phoenixguard.windows_region_capture")
_ERROR_ALREADY_EXISTS = 183


def _default_runtime_dir() -> Path:
    configured = str(os.getenv("PHOENIXGUARD_RUNTIME_DIR", "") or "").strip()
    return Path(configured) if configured else PROJECT_ROOT / "runtime" / "live"


def _default_status_path() -> Path:
    configured = str(os.getenv("PHOENIXGUARD_WINDOWS_REGION_CAPTURE_STATUS_FILE", "") or "").strip()
    return Path(configured) if configured else _default_runtime_dir() / "windows_region_capture_status.json"


def _default_base_url() -> str:
    configured = str(os.getenv("PHOENIXGUARD_MOBILE_API_CLIENT_BASE_URL", "") or "").strip()
    if configured:
        return configured.rstrip("/")
    port = int(os.getenv("PHOENIXGUARD_MOBILE_API_PORT", "8793") or "8793")
    return f"http://127.0.0.1:{port}"


def _default_restore_binding_path() -> Path | None:
    configured = str(os.getenv("PHOENIXGUARD_WINDOWS_REGION_RESTORE_BINDING_FILE", "") or "").strip()
    return Path(configured) if configured else None


def _write_launcher_status(path: Path, status: str, message: str, **values: Any) -> None:
    payload = {
        "schema_version": "PG_WINDOWS_REGION_CAPTURE_STATUS_V3",
        "status": str(status),
        "message": str(message),
        "source_live": False,
        "updated_epoch": time.time(),
        **values,
    }
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def build_runtime_readiness_v3() -> dict[str, Any]:
    installed = windows_capture_runtime_version_v3()
    result: dict[str, Any] = {
        "schema_version": "PG_WINDOWS_REGION_CAPTURE_READINESS_V3",
        "ok": False,
        "platform": platform.system(),
        "python": platform.python_version(),
        "runtime_distribution": WGC_RUNTIME_DISTRIBUTION,
        "required_runtime_version": WGC_RUNTIME_VERSION,
        "installed_runtime_version": installed,
        "capture_backend": "Windows Graphics Capture by exact HWND",
        "fallback_backend": None,
    }
    try:
        require_windows_capture_runtime_v3()
    except Exception as exc:
        result["reason"] = str(exc)
        return result
    result["ok"] = True
    result["reason"] = "The optional exact-HWND Windows Graphics Capture runtime is ready."
    return result


class _SingleInstanceMutex:
    def __init__(self, handle: int) -> None:
        self._handle = int(handle)

    def close(self) -> None:
        if self._handle <= 0 or os.name != "nt":
            return
        ctypes.windll.kernel32.CloseHandle(wintypes.HANDLE(self._handle))
        self._handle = 0


def _acquire_single_instance_mutex(session_id: str) -> _SingleInstanceMutex:
    if os.name != "nt":
        raise RuntimeError("The Windows region capture agent requires Windows.")
    digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:20]
    mutex_name = f"Local\\PhoenixGuardWindowsRegionCaptureV3-{digest}"
    kernel32 = ctypes.windll.kernel32
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    create_mutex.restype = wintypes.HANDLE
    handle = create_mutex(None, False, mutex_name)
    if not handle:
        raise OSError("Windows refused the region-capture singleton mutex.")
    if int(kernel32.GetLastError()) == _ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        raise RuntimeError("A Windows region capture agent already owns this PhoenixGuard session.")
    return _SingleInstanceMutex(int(handle))


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the exact-HWND PhoenixGuard Windows region streaming agent."
    )
    parser.add_argument("--base-url", default=_default_base_url())
    parser.add_argument(
        "--session-id",
        default=str(os.getenv("PHOENIXGUARD_TRACKER_SESSION_ID", "pocket-live-8788") or "pocket-live-8788"),
    )
    parser.add_argument("--status-path", type=Path, default=_default_status_path())
    parser.add_argument(
        "--upload-interval",
        type=float,
        default=float(os.getenv("PHOENIXGUARD_WINDOWS_REGION_UPLOAD_INTERVAL_SEC", "4.0") or "4.0"),
    )
    parser.add_argument(
        "--freshness-timeout",
        type=float,
        default=float(os.getenv("PHOENIXGUARD_WINDOWS_REGION_FRESHNESS_TIMEOUT_SEC", "12.0") or "12.0"),
    )
    parser.add_argument(
        "--update-ms",
        type=int,
        default=int(os.getenv("PHOENIXGUARD_WINDOWS_REGION_WGC_UPDATE_MS", "1000") or "1000"),
    )
    parser.add_argument(
        "--restore-binding",
        type=Path,
        default=_default_restore_binding_path(),
        help="Restore a previously verified public WGC binding without reopening the region selector.",
    )
    parser.add_argument("--readiness-check", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    status_path = Path(args.status_path)
    readiness = build_runtime_readiness_v3()
    if args.readiness_check:
        print(json.dumps(readiness, sort_keys=True), flush=True)
        return 0 if bool(readiness.get("ok")) else 2
    if not bool(readiness.get("ok")):
        _write_launcher_status(
            status_path,
            "runtime_unavailable",
            str(readiness.get("reason", "The Windows capture runtime is unavailable.")),
            readiness=readiness,
        )
        LOGGER.error("%s", readiness.get("reason"))
        return 2

    assert_repo_venv_runtime("windows_region_capture", PROJECT_ROOT)
    token = str(os.getenv("PHOENIXGUARD_FRAME_INGEST_TOKEN", "") or "").strip()
    if not token:
        message = "The WGC agent refused to start without PHOENIXGUARD_FRAME_INGEST_TOKEN."
        _write_launcher_status(status_path, "not_armed", message)
        LOGGER.error(message)
        return 2

    logs_dir = Path(os.getenv("PHOENIXGUARD_LOGS_DIR") or _default_runtime_dir() / "logs_live")
    logs_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(logs_dir / "windows_region_capture.log", encoding="utf-8"),
        ],
        force=True,
    )

    mutex: _SingleInstanceMutex | None = None
    manager: WindowsRegionCaptureManagerV3 | None = None
    hotkeys: GlobalRegionHotkeyLoopV3 | None = None
    fatal_error = ""
    restore_payload: dict[str, Any] = {}
    if args.restore_binding is not None:
        try:
            parsed_restore = json.loads(Path(args.restore_binding).read_text(encoding="utf-8"))
            if not isinstance(parsed_restore, dict):
                raise ValueError("The restore binding must be a JSON object.")
            restore_payload = dict(parsed_restore)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            LOGGER.warning("The saved Windows region binding could not be loaded: %s", exc)
    try:
        mutex = _acquire_single_instance_mutex(str(args.session_id))
        ingest_client = PhoenixGuardRegionIngestClientV3(
            base_url=str(args.base_url),
            session_id=str(args.session_id),
            token=token,
        )
        manager = WindowsRegionCaptureManagerV3(
            ingest_client=ingest_client,
            status_path=status_path,
            upload_interval_sec=float(args.upload_interval),
            freshness_timeout_sec=float(args.freshness_timeout),
            minimum_update_interval_ms=int(args.update_ms),
        )
        manager.start()
        if restore_payload:
            if manager.restore_public_binding(restore_payload):
                LOGGER.info("The exact saved HWND and chart ROI were restored without reopening the selector.")
            else:
                LOGGER.warning("The saved chart ROI was not restored; the global selection hotkey remains ready.")
        hotkeys = GlobalRegionHotkeyLoopV3(
            on_select=manager.select_foreground_source,
            on_kill=manager.kill_active_source,
            on_registration=manager.report_hotkey_registration,
        )
        LOGGER.info(
            "Windows region capture is ready: Ctrl+Shift+B selects or switches; Ctrl+Shift+K stops it."
        )
        hotkeys.run()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception as exc:
        fatal_error = str(exc)[:500]
        LOGGER.exception("The Windows region capture agent stopped.")
        return 1
    finally:
        if hotkeys is not None:
            hotkeys.stop()
        if manager is not None:
            manager.shutdown()
        if mutex is not None:
            mutex.close()
        if fatal_error:
            # Manager shutdown publishes its normal terminal state, so write the
            # fatal launcher state last and keep the operator-visible cause.
            _write_launcher_status(status_path, "error", fatal_error)


if __name__ == "__main__":
    raise SystemExit(main())
