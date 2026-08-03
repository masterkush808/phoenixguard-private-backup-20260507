from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import import_module
from io import BytesIO
import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any, Protocol, cast
from uuid import uuid4

import numpy as np
from PIL import Image
import psutil
import requests


LOGGER = logging.getLogger(__name__)

WGC_RUNTIME_DISTRIBUTION = "windows-capture"
WGC_RUNTIME_VERSION = "2.0.0"
WGC_COORDINATE_SPACE = "wgc_hwnd_roi_v1"
WGC_SOURCE_TYPE = "windows_graphics_capture_roi"
WGC_SOURCE_ID = "windows-region-capture-v3"
WGC_STATUS_SCHEMA = "PG_WINDOWS_REGION_CAPTURE_STATUS_V3"
WGC_BINDING_SCHEMA = "PG_WINDOWS_REGION_CAPTURE_BINDING_V3"

SELECT_HOTKEY = "Ctrl+Shift+B"
KILL_HOTKEY = "Ctrl+Shift+K"

_GA_ROOT = 2
_MOD_CONTROL = 0x0002
_MOD_SHIFT = 0x0004
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012
_HOTKEY_SELECT_ID = 0x8081
_HOTKEY_KILL_ID = 0x8082
_SW_SHOWNOACTIVATE = 4
_HWND_BOTTOM = 1
_SWP_NOSIZE = 0x0001
_SWP_NOMOVE = 0x0002
_SWP_NOACTIVATE = 0x0010
_SWP_ASYNCWINDOWPOS = 0x4000


class WgcRuntimeUnavailableError(RuntimeError):
    """Raised when the optional Windows Graphics Capture runtime is unavailable."""


class SourceLeaseLostError(RuntimeError):
    """Raised when the server rejects a superseded or killed source lease."""


class SourceSelectionCancelled(RuntimeError):
    """Raised when the operator cancels an in-progress source selection."""


class FrameUploadDeferredError(RuntimeError):
    """Raised when the server asks the source to retry after a bounded delay."""

    def __init__(self, message: str, *, retry_after_sec: float) -> None:
        super().__init__(message)
        self.retry_after_sec = max(1.0, float(retry_after_sec))


class _CaptureControl(Protocol):
    def stop(self) -> None:
        ...


class _HttpResponse(Protocol):
    status_code: int
    headers: Mapping[str, str]

    def json(self) -> Any:
        ...

    def raise_for_status(self) -> None:
        ...


class _HttpSession(Protocol):
    def get(self, url: str, **kwargs: Any) -> _HttpResponse:
        ...

    def post(self, url: str, **kwargs: Any) -> _HttpResponse:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True, slots=True)
class WindowIdentityV3:
    hwnd: int
    process_id: int
    process_create_time: float
    process_path: str
    class_name: str
    title: str
    rect: tuple[int, int, int, int]
    is_visible: bool
    is_minimized: bool

    @property
    def width(self) -> int:
        return max(0, int(self.rect[2]) - int(self.rect[0]))

    @property
    def height(self) -> int:
        return max(0, int(self.rect[3]) - int(self.rect[1]))

    def same_target(self, other: WindowIdentityV3) -> bool:
        """Reject HWND reuse while allowing browser-title and geometry drift."""

        return bool(
            int(self.hwnd) > 0
            and int(self.hwnd) == int(other.hwnd)
            and int(self.process_id) == int(other.process_id)
            and abs(float(self.process_create_time) - float(other.process_create_time)) < 0.01
            and str(self.class_name) == str(other.class_name)
            and str(self.process_path).casefold() == str(other.process_path).casefold()
        )

    def public_payload(self) -> dict[str, Any]:
        return {
            "hwnd": int(self.hwnd),
            "process_id": int(self.process_id),
            "process_create_time": float(self.process_create_time),
            "process_path": str(self.process_path),
            "class_name": str(self.class_name),
            "title": str(self.title),
            "window_rect": [int(value) for value in self.rect],
            "is_visible": bool(self.is_visible),
            "is_minimized": bool(self.is_minimized),
        }


@dataclass(frozen=True, slots=True)
class CapturedWindowFrameV3:
    local_generation: int
    frame_id: int
    captured_epoch: float
    qpc_timespan: int
    image: Image.Image


class LatestFrameSlotV3:
    """A one-frame mailbox: newer frames replace older unconsumed frames."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._changed = threading.Event()
        self._latest: CapturedWindowFrameV3 | None = None

    def publish(self, frame: CapturedWindowFrameV3) -> None:
        with self._lock:
            if self._latest is None or int(frame.frame_id) > int(self._latest.frame_id):
                self._latest = frame
                self._changed.set()

    def latest(self) -> CapturedWindowFrameV3 | None:
        with self._lock:
            return self._latest

    def wait_for_frame(self, *, after_frame_id: int = 0, timeout: float = 10.0) -> CapturedWindowFrameV3 | None:
        deadline = time.monotonic() + max(0.0, float(timeout))
        while True:
            with self._lock:
                latest = self._latest
                if latest is not None and int(latest.frame_id) > int(after_frame_id):
                    return latest
                self._changed.clear()
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                return None
            self._changed.wait(timeout=remaining)


@dataclass(frozen=True, slots=True)
class SourceLeaseV3:
    source_generation: int
    source_lease_id: str


@dataclass(frozen=True, slots=True)
class RegionSelectionV3:
    identity: WindowIdentityV3
    normalized_bbox: tuple[float, float, float, float]
    selection_id: str
    sequence_id: str
    reference_frame_size: tuple[int, int]

    @property
    def display_name(self) -> str:
        title = str(self.identity.title or "Selected window").strip()
        return title[:160] or "Selected window"


@dataclass(frozen=True, slots=True)
class RegionBindingV3:
    selection: RegionSelectionV3
    lease: SourceLeaseV3

    def public_payload(self) -> dict[str, Any]:
        return {
            "schema_version": WGC_BINDING_SCHEMA,
            "source_id": WGC_SOURCE_ID,
            "source_type": WGC_SOURCE_TYPE,
            "coordinate_space": WGC_COORDINATE_SPACE,
            "source_generation": int(self.lease.source_generation),
            "selection_id": str(self.selection.selection_id),
            "sequence_id": str(self.selection.sequence_id),
            "display_name": self.selection.display_name,
            "normalized_bbox": [float(value) for value in self.selection.normalized_bbox],
            "reference_frame_size": [int(value) for value in self.selection.reference_frame_size],
            "window": self.selection.identity.public_payload(),
        }

    def frame_metadata(
        self,
        *,
        full_frame_size: tuple[int, int],
        roi_size: tuple[int, int],
        geometry_generation: int,
        qpc_timespan: int,
    ) -> dict[str, Any]:
        payload = self.public_payload()
        full_width, full_height = (max(1, int(value)) for value in full_frame_size)
        left, top, right, bottom = self.selection.normalized_bbox
        pixel_left = max(0, min(full_width - 1, int(round(full_width * left))))
        pixel_top = max(0, min(full_height - 1, int(round(full_height * top))))
        pixel_right = max(pixel_left + 1, min(full_width, int(round(full_width * right))))
        pixel_bottom = max(pixel_top + 1, min(full_height, int(round(full_height * bottom))))
        payload.update(
            {
                "source_lease_id": str(self.lease.source_lease_id),
                "full_frame_size": [full_width, full_height],
                "roi_size": [int(value) for value in roi_size],
                "roi_normalized": [float(value) for value in self.selection.normalized_bbox],
                "roi_source_pixels": {
                    "x": pixel_left,
                    "y": pixel_top,
                    "width": pixel_right - pixel_left,
                    "height": pixel_bottom - pixel_top,
                },
                "source_surface_width": full_width,
                "source_surface_height": full_height,
                "source_render_fresh": True,
                "transport_frame_age_ms": 0,
                "visual_change_age_ms": 0,
                "geometry_generation": int(geometry_generation),
                "geometry_epoch": int(geometry_generation),
                "qpc_timespan": int(qpc_timespan),
                "focus_policy": "hwnd_wgc_no_activation",
                "browser_chrome_included": True,
                "broker_click_authority": False,
            }
        )
        return payload


@dataclass(slots=True)
class ActiveRegionSourceV3:
    local_generation: int
    binding: RegionBindingV3
    stream: WindowsGraphicsCaptureStreamV3
    last_uploaded_frame_id: int = 0
    last_uploaded_epoch: float = 0.0
    last_full_frame_size: tuple[int, int] = (0, 0)
    geometry_generation: int = 1
    selected_epoch: float = field(default_factory=time.time)
    next_upload_attempt_epoch: float = 0.0
    consecutive_upload_failures: int = 0
    last_health_status: str = "validating"
    last_identity_check_epoch: float = 0.0
    last_verified_minimized: bool = False
    background_restore_count: int = 0
    last_background_restore_epoch: float = 0.0
    stream_restart_count: int = 0
    last_stream_restart_epoch: float = 0.0


_SELECTOR_PROCESS_LOCK = threading.Lock()
_ACTIVE_SELECTOR_PROCESSES: set[subprocess.Popen[str]] = set()


def normalize_region_bbox_v3(
    values: Sequence[Any],
    *,
    min_fraction: float = 0.02,
) -> tuple[float, float, float, float]:
    if len(values) < 4:
        raise ValueError("A selected region requires four normalized coordinates.")
    left = max(0.0, min(1.0, float(values[0])))
    top = max(0.0, min(1.0, float(values[1])))
    right = max(left, min(1.0, float(values[2])))
    bottom = max(top, min(1.0, float(values[3])))
    if right - left < float(min_fraction) or bottom - top < float(min_fraction):
        raise ValueError("The selected region is too small to stream reliably.")
    return left, top, right, bottom


def crop_normalized_region_v3(
    image: Image.Image,
    normalized_bbox: Sequence[Any],
) -> Image.Image:
    left, top, right, bottom = normalize_region_bbox_v3(normalized_bbox, min_fraction=0.001)
    x0 = max(0, min(image.width - 1, int(round(image.width * left))))
    y0 = max(0, min(image.height - 1, int(round(image.height * top))))
    x1 = max(x0 + 1, min(image.width, int(round(image.width * right))))
    y1 = max(y0 + 1, min(image.height, int(round(image.height * bottom))))
    return image.crop((x0, y0, x1, y1)).convert("RGB")


def read_window_identity_v3(hwnd: int) -> WindowIdentityV3:
    if os.name != "nt":
        raise WgcRuntimeUnavailableError("Windows region capture is available only on Windows.")
    user32 = ctypes.windll.user32
    normalized_hwnd = int(hwnd or 0)
    if normalized_hwnd <= 0 or not bool(user32.IsWindow(normalized_hwnd)):
        raise ValueError("The selected window is no longer available.")

    root_hwnd = int(user32.GetAncestor(normalized_hwnd, _GA_ROOT) or normalized_hwnd)
    if root_hwnd <= 0 or not bool(user32.IsWindow(root_hwnd)):
        raise ValueError("The selected top-level window is no longer available.")

    process_id = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(root_hwnd, ctypes.byref(process_id))
    pid = int(process_id.value)
    if pid <= 0:
        raise ValueError("The selected window has no verifiable process identity.")

    title_length = max(0, int(user32.GetWindowTextLengthW(root_hwnd)))
    title_buffer = ctypes.create_unicode_buffer(title_length + 1)
    user32.GetWindowTextW(root_hwnd, title_buffer, title_length + 1)
    class_buffer = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(root_hwnd, class_buffer, len(class_buffer))
    rect = wintypes.RECT()
    if not bool(user32.GetWindowRect(root_hwnd, ctypes.byref(rect))):
        raise ValueError("The selected window geometry is unavailable.")

    process = psutil.Process(pid)
    try:
        process_path = str(process.exe() or "")
    except (psutil.AccessDenied, psutil.ZombieProcess):
        process_path = str(process.name() or "")
    return WindowIdentityV3(
        hwnd=root_hwnd,
        process_id=pid,
        process_create_time=float(process.create_time()),
        process_path=process_path,
        class_name=str(class_buffer.value or ""),
        title=str(title_buffer.value or ""),
        rect=(int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)),
        is_visible=bool(user32.IsWindowVisible(root_hwnd)),
        is_minimized=bool(user32.IsIconic(root_hwnd)),
    )


def foreground_window_identity_v3() -> WindowIdentityV3:
    if os.name != "nt":
        raise WgcRuntimeUnavailableError("Windows region capture is available only on Windows.")
    hwnd = int(ctypes.windll.user32.GetForegroundWindow() or 0)
    if hwnd <= 0:
        raise ValueError("No foreground window is available for selection.")
    identity = read_window_identity_v3(hwnd)
    if int(identity.process_id) == int(os.getpid()):
        raise ValueError("PhoenixGuard cannot select its own capture controls as a source.")
    if not identity.is_visible or identity.is_minimized or identity.width < 64 or identity.height < 64:
        raise ValueError("Bring a normal, non-minimized chart window into view before selecting it.")
    return identity


def restore_window_for_background_capture_v3(
    identity: WindowIdentityV3,
    *,
    user32: Any | None = None,
    wait_timeout_sec: float = 0.75,
) -> bool:
    """Make an exact HWND renderable again without taking keyboard focus.

    Windows Graphics Capture is independent of z-order, but Windows suspends a
    window capture item while its target is minimized.  Restoring with
    ``SW_SHOWNOACTIVATE`` keeps the current application in the foreground; the
    target is then placed at the bottom of the z-order so it remains a
    background surface instead of popping over the operator's work.

    This intentionally has no desktop-pixel, title-search, or foreground
    fallback.  The previously attested HWND remains the only capture target.
    """

    if int(identity.hwnd) <= 0:
        return False
    if user32 is None:
        if os.name != "nt":
            return False
        user32 = ctypes.windll.user32
    hwnd = int(identity.hwnd)
    if not bool(user32.IsWindow(hwnd)):
        return False
    needs_restore = bool(user32.IsIconic(hwnd)) or not bool(user32.IsWindowVisible(hwnd))
    if not needs_restore:
        return True

    foreground_before = int(user32.GetForegroundWindow() or 0)
    # The return from ShowWindowAsync describes the old visibility state, not
    # whether the asynchronous request was accepted, so verify final state.
    user32.ShowWindowAsync(hwnd, _SW_SHOWNOACTIVATE)
    user32.SetWindowPos(
        hwnd,
        _HWND_BOTTOM,
        0,
        0,
        0,
        0,
        _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE | _SWP_ASYNCWINDOWPOS,
    )
    deadline = time.monotonic() + max(0.05, float(wait_timeout_sec))
    foreground_preserved = True
    while time.monotonic() < deadline:
        foreground_preserved = bool(
            foreground_preserved
            and int(user32.GetForegroundWindow() or 0) == foreground_before
        )
        if not bool(user32.IsIconic(hwnd)) and bool(user32.IsWindowVisible(hwnd)):
            break
        time.sleep(0.025)
    renderable = bool(
        user32.IsWindow(hwnd)
        and not bool(user32.IsIconic(hwnd))
        and bool(user32.IsWindowVisible(hwnd))
    )
    if renderable:
        # Repeat after the async show has completed so the recovered source
        # cannot rise above the application the operator is actually using.
        user32.SetWindowPos(
            hwnd,
            _HWND_BOTTOM,
            0,
            0,
            0,
            0,
            _SWP_NOSIZE | _SWP_NOMOVE | _SWP_NOACTIVATE | _SWP_ASYNCWINDOWPOS,
        )
    foreground_after = int(user32.GetForegroundWindow() or 0)
    return bool(
        renderable
        and foreground_preserved
        and foreground_after == foreground_before
    )


def windows_capture_runtime_version_v3() -> str:
    try:
        from importlib import metadata

        return str(metadata.version(WGC_RUNTIME_DISTRIBUTION))
    except Exception:
        return ""


def require_windows_capture_runtime_v3() -> None:
    if os.name != "nt":
        raise WgcRuntimeUnavailableError("Windows region capture is available only on Windows.")
    installed = windows_capture_runtime_version_v3()
    if installed != WGC_RUNTIME_VERSION:
        installed_label = installed or "not installed"
        raise WgcRuntimeUnavailableError(
            f"{WGC_RUNTIME_DISTRIBUTION}=={WGC_RUNTIME_VERSION} is required; found {installed_label}."
        )
    try:
        import_module("windows_capture")
    except Exception as exc:
        raise WgcRuntimeUnavailableError(
            "The Windows Graphics Capture runtime could not be imported."
        ) from exc


class WindowsGraphicsCaptureStreamV3:
    """An HWND-bound WGC stream with no desktop or foreground fallback."""

    def __init__(
        self,
        identity: WindowIdentityV3,
        *,
        local_generation: int,
        minimum_update_interval_ms: int = 1000,
        capture_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.identity = identity
        self.local_generation = int(local_generation)
        self.minimum_update_interval_ms = max(250, int(minimum_update_interval_ms))
        self.slot = LatestFrameSlotV3()
        self._capture_factory = capture_factory
        self._capture: Any = None
        self._control: _CaptureControl | None = None
        self._lock = threading.Lock()
        self._frame_id = 0
        self._started = False
        self._closed = threading.Event()
        self._last_error = ""

    @property
    def closed(self) -> bool:
        return self._closed.is_set()

    @property
    def last_error(self) -> str:
        with self._lock:
            return self._last_error

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
        if self._capture_factory is None:
            require_windows_capture_runtime_v3()
            capture_type = getattr(import_module("windows_capture"), "WindowsCapture")
            capture_factory = cast(Callable[..., Any], capture_type)
        else:
            capture_factory = self._capture_factory

        try:
            capture = capture_factory(
                cursor_capture=False,
                draw_border=False,
                secondary_window=False,
                minimum_update_interval=self.minimum_update_interval_ms,
                dirty_region=False,
                window_hwnd=int(self.identity.hwnd),
            )

            @capture.event
            def on_frame_arrived(frame: Any, _capture_control: Any) -> None:
                try:
                    pixels = np.asarray(frame.frame_buffer, dtype=np.uint8)
                    if pixels.ndim != 3 or pixels.shape[2] < 3:
                        raise ValueError("WGC returned an invalid pixel buffer.")
                    rgb = pixels[:, :, :3][:, :, ::-1].copy()
                    image = Image.fromarray(rgb, mode="RGB")
                    with self._lock:
                        self._frame_id += 1
                        frame_id = self._frame_id
                        self._last_error = ""
                    self.slot.publish(
                        CapturedWindowFrameV3(
                            local_generation=self.local_generation,
                            frame_id=frame_id,
                            captured_epoch=time.time(),
                            qpc_timespan=int(getattr(frame, "timespan", 0) or 0),
                            image=image,
                        )
                    )
                except Exception as exc:
                    with self._lock:
                        self._last_error = str(exc)

            @capture.event
            def on_closed() -> None:
                self._closed.set()

            # The dynamic event decorator retains both callbacks; this explicit
            # reference also makes that ownership visible to static analysis.
            _event_callbacks = (on_frame_arrived, on_closed)
            control = cast(_CaptureControl, capture.start_free_threaded())
            self._capture = capture
            self._control = control
            del _event_callbacks
        except Exception:
            with self._lock:
                self._started = False
            self._closed.set()
            raise

    def wait_first_frame(self, *, timeout: float = 12.0) -> CapturedWindowFrameV3:
        frame = self.slot.wait_for_frame(timeout=timeout)
        if frame is None:
            detail = self.last_error or "No WGC frame arrived before the selection timeout."
            raise WgcRuntimeUnavailableError(detail)
        return frame

    def seed_frame_id(self, after_frame_id: int) -> None:
        """Keep source-frame identity monotonic when a WGC session is rebuilt."""

        with self._lock:
            if self._started:
                raise RuntimeError("A running WGC stream cannot be reseeded.")
            self._frame_id = max(self._frame_id, int(after_frame_id))

    def stop(self) -> None:
        control: _CaptureControl | None
        with self._lock:
            control = self._control
            self._control = None
            self._capture = None
            self._started = False
        if control is not None:
            try:
                control.stop()
            except Exception:
                LOGGER.debug("WGC capture control did not stop cleanly.", exc_info=True)
        self._closed.set()


def run_native_region_selector_v3(
    identity: WindowIdentityV3,
    first_frame: CapturedWindowFrameV3,
    *,
    timeout_sec: float = 190.0,
) -> tuple[float, float, float, float]:
    helper_path = Path(__file__).with_name("native_source_region_overlay.py")
    if not helper_path.exists():
        raise RuntimeError(f"The native source selector is missing: {helper_path}")
    temporary_path = ""
    try:
        with tempfile.NamedTemporaryFile(prefix="phoenixguard-wgc-select-", suffix=".png", delete=False) as handle:
            temporary_path = handle.name
        first_frame.image.save(temporary_path, format="PNG")
        command = [
            str(sys.executable),
            str(helper_path),
            "--hwnd",
            str(int(identity.hwnd)),
            "--frame-path",
            temporary_path,
        ]
        creation_flags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0)) if os.name == "nt" else 0
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            creationflags=creation_flags,
        )
        with _SELECTOR_PROCESS_LOCK:
            _ACTIVE_SELECTOR_PROCESSES.add(process)
        try:
            try:
                stdout, stderr = process.communicate(timeout=max(15.0, float(timeout_sec)))
            except subprocess.TimeoutExpired as exc:
                process.terminate()
                try:
                    process.communicate(timeout=5.0)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate(timeout=5.0)
                raise SourceSelectionCancelled("Windows region selection timed out.") from exc
        finally:
            with _SELECTOR_PROCESS_LOCK:
                _ACTIVE_SELECTOR_PROCESSES.discard(process)
        parsed: dict[str, Any] = {}
        for line in reversed(str(stdout or "").splitlines()):
            try:
                candidate = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, Mapping):
                parsed = dict(cast(Mapping[str, Any], candidate))
                break
        status = str(parsed.get("status", "") or "").lower()
        if status == "cancelled":
            raise SourceSelectionCancelled(str(parsed.get("message", "Selection cancelled.")))
        if status != "selected":
            detail = str(parsed.get("message", "") or stderr or "Source selection failed.")
            raise RuntimeError(detail[:500])
        return normalize_region_bbox_v3(cast(Sequence[Any], parsed.get("normalized_bbox", [])))
    finally:
        if temporary_path:
            try:
                Path(temporary_path).unlink(missing_ok=True)
            except OSError:
                LOGGER.debug("Unable to remove the temporary WGC selector frame.", exc_info=True)


def cancel_native_region_selector_v3() -> int:
    """Close only selector subprocesses owned by this capture agent."""

    with _SELECTOR_PROCESS_LOCK:
        processes = list(_ACTIVE_SELECTOR_PROCESSES)
    stopped = 0
    for process in processes:
        if process.poll() is not None:
            continue
        try:
            process.terminate()
            stopped += 1
        except OSError:
            LOGGER.debug("Unable to terminate the native region selector.", exc_info=True)
    return stopped


class PhoenixGuardRegionIngestClientV3:
    def __init__(
        self,
        *,
        base_url: str,
        session_id: str,
        token: str,
        http_session: _HttpSession | None = None,
        timeout_sec: float = 30.0,
    ) -> None:
        self.base_url = str(base_url or "http://127.0.0.1:8793").rstrip("/")
        self.session_id = str(session_id or "pocket-live-8788").strip()
        self._token = str(token or "").strip()
        if not self._token:
            raise ValueError("A local frame-ingest token is required for WGC capture.")
        self._http = http_session or cast(_HttpSession, requests.Session())
        self.timeout_sec = max(2.0, float(timeout_sec))

    @property
    def _headers(self) -> dict[str, str]:
        return {"X-PhoenixGuard-Token": self._token}

    def claim_source(
        self,
        selection: RegionSelectionV3,
        *,
        expected_source_control: Mapping[str, Any] | None = None,
    ) -> SourceLeaseV3:
        claim_payload: dict[str, Any] = {
            "source_id": WGC_SOURCE_ID,
            "sequence_id": selection.sequence_id,
            "source_type": WGC_SOURCE_TYPE,
            "selection_id": selection.selection_id,
            "display_name": selection.display_name,
            "coordinate_space": WGC_COORDINATE_SPACE,
        }
        if expected_source_control is not None:
            claim_payload["expected_source_control"] = dict(expected_source_control)
        response = self._http.post(
            f"{self.base_url}/v1/mobile/frame-ingest/sessions/{self.session_id}/source-control/claim",
            headers=self._headers,
            json=claim_payload,
            timeout=self.timeout_sec,
        )
        self._raise_for_lease_status(response)
        response.raise_for_status()
        payload_value = response.json()
        if not isinstance(payload_value, Mapping):
            raise RuntimeError("Source claim returned an invalid response.")
        payload = cast(Mapping[str, Any], payload_value)
        generation = int(payload.get("source_generation", 0) or 0)
        lease_id = str(payload.get("source_lease_id", "") or "").strip()
        if generation <= 0 or not lease_id:
            raise RuntimeError("Source claim did not return a generation and lease.")
        return SourceLeaseV3(source_generation=generation, source_lease_id=lease_id)

    def get_source_control(self) -> Mapping[str, Any]:
        """Read the public capture-source fence without exposing its lease."""

        response = self._http.get(
            f"{self.base_url}/v1/mobile/frame-ingest/sessions/{self.session_id}/source-control",
            headers=self._headers,
            timeout=self.timeout_sec,
        )
        response.raise_for_status()
        payload_value = response.json()
        if not isinstance(payload_value, Mapping):
            raise RuntimeError("Source status returned an invalid response.")
        payload = cast(Mapping[str, Any], payload_value)
        source_value = payload.get("source_control")
        if not isinstance(source_value, Mapping):
            raise RuntimeError("Source status did not include capture_source_v3.")
        return dict(cast(Mapping[str, Any], source_value))

    def kill_source(self, binding: RegionBindingV3, *, reason: str) -> None:
        response = self._http.post(
            f"{self.base_url}/v1/mobile/frame-ingest/sessions/{self.session_id}/source-control/kill",
            headers=self._headers,
            json={
                "source_id": WGC_SOURCE_ID,
                "sequence_id": binding.selection.sequence_id,
                "source_generation": int(binding.lease.source_generation),
                "source_lease_id": binding.lease.source_lease_id,
                "reason": str(reason or "operator_stop")[:160],
            },
            timeout=self.timeout_sec,
        )
        if int(response.status_code) not in {404, 409, 410}:
            response.raise_for_status()

    def upload_frame(
        self,
        active: ActiveRegionSourceV3,
        frame: CapturedWindowFrameV3,
        roi: Image.Image,
    ) -> Mapping[str, Any]:
        encoded = BytesIO()
        roi.convert("RGB").save(encoded, format="JPEG", quality=85, optimize=False)
        metadata = active.binding.frame_metadata(
            full_frame_size=frame.image.size,
            roi_size=roi.size,
            geometry_generation=active.geometry_generation,
            qpc_timespan=frame.qpc_timespan,
        )
        response = self._http.post(
            f"{self.base_url}/v1/mobile/frame-ingest/sessions/{self.session_id}/frames",
            headers=self._headers,
            files={"frame": ("wgc-region.jpg", encoded.getvalue(), "image/jpeg")},
            data={
                "source_id": WGC_SOURCE_ID,
                "symbol": "",
                "timeframe": "",
                "source_url": "",
                "sequence_id": active.binding.selection.sequence_id,
                "capture_epoch_ms": str(int(round(frame.captured_epoch * 1000.0))),
                "frame_id": str(int(frame.frame_id)),
                "source_generation": str(int(active.binding.lease.source_generation)),
                "source_lease_id": active.binding.lease.source_lease_id,
                "metadata_json": json.dumps(metadata, separators=(",", ":"), sort_keys=True),
            },
            timeout=self.timeout_sec,
        )
        self._raise_for_lease_status(response)
        if int(response.status_code) == 429:
            raw_retry_after = str(response.headers.get("Retry-After", "1") or "1").strip()
            try:
                retry_after = float(raw_retry_after)
            except ValueError:
                retry_after = 1.0
            raise FrameUploadDeferredError(
                "The frame-ingest rate limit deferred the latest WGC frame.",
                retry_after_sec=retry_after + 0.25,
            )
        response.raise_for_status()
        payload = response.json()
        return dict(cast(Mapping[str, Any], payload)) if isinstance(payload, Mapping) else {}

    @staticmethod
    def _raise_for_lease_status(response: _HttpResponse) -> None:
        if int(response.status_code) in {409, 410}:
            raise SourceLeaseLostError(
                f"The WGC source lease is no longer current (HTTP {response.status_code})."
            )

    def close(self) -> None:
        self._http.close()


class WindowsRegionCaptureManagerV3:
    """Own selection, lease, source switching, and bounded frame delivery."""

    def __init__(
        self,
        *,
        ingest_client: PhoenixGuardRegionIngestClientV3,
        status_path: Path,
        identity_reader: Callable[[int], WindowIdentityV3] = read_window_identity_v3,
        foreground_reader: Callable[[], WindowIdentityV3] = foreground_window_identity_v3,
        selector: Callable[[WindowIdentityV3, CapturedWindowFrameV3], tuple[float, float, float, float]] = run_native_region_selector_v3,
        stream_factory: Callable[[WindowIdentityV3, int], WindowsGraphicsCaptureStreamV3] | None = None,
        background_restorer: Callable[[WindowIdentityV3], bool] = restore_window_for_background_capture_v3,
        upload_interval_sec: float = 4.0,
        freshness_timeout_sec: float = 12.0,
        minimum_update_interval_ms: int = 1000,
        identity_check_interval_sec: float = 1.0,
    ) -> None:
        self.ingest_client = ingest_client
        self.status_path = Path(status_path)
        self.identity_reader = identity_reader
        self.foreground_reader = foreground_reader
        self.selector = selector
        self.background_restorer = background_restorer
        self.upload_interval_sec = max(1.0, float(upload_interval_sec))
        self.freshness_timeout_sec = max(self.upload_interval_sec * 2.0, float(freshness_timeout_sec))
        self.minimum_update_interval_ms = max(250, int(minimum_update_interval_ms))
        self.identity_check_interval_sec = max(0.25, float(identity_check_interval_sec))
        self._stream_factory = stream_factory
        self._lock = threading.RLock()
        self._selection_lock = threading.Lock()
        self._active: ActiveRegionSourceV3 | None = None
        self._local_generation = 0
        self._operation_epoch = 0
        self._claim_in_progress = False
        self._stop_evt = threading.Event()
        self._uploader_thread: threading.Thread | None = None
        self._hotkey_registered = False
        self._hotkey_error = ""
        self._status: dict[str, Any] = {
            "schema_version": WGC_STATUS_SCHEMA,
            "status": "ready",
            "message": f"Press {SELECT_HOTKEY} to select a chart region.",
            "select_hotkey": SELECT_HOTKEY,
            "kill_hotkey": KILL_HOTKEY,
            "source_live": False,
            "hotkey_registered": False,
            "hotkey_error": "",
            "updated_epoch": time.time(),
        }
        self._write_status()

    def start(self) -> None:
        with self._lock:
            if self._uploader_thread is not None and self._uploader_thread.is_alive():
                return
            self._stop_evt.clear()
            self._uploader_thread = threading.Thread(
                target=self._uploader_loop,
                name="phoenixguard-wgc-region-uploader",
                daemon=True,
            )
            self._uploader_thread.start()

    def report_hotkey_registration(self, registered: bool, error: str = "") -> None:
        with self._lock:
            self._hotkey_registered = bool(registered)
            self._hotkey_error = str(error or "")[:500]
            self._status["hotkey_registered"] = self._hotkey_registered
            self._status["hotkey_error"] = self._hotkey_error
            self._status["updated_epoch"] = time.time()
        self._write_status()

    def _new_stream(self, identity: WindowIdentityV3, local_generation: int) -> WindowsGraphicsCaptureStreamV3:
        if self._stream_factory is not None:
            return self._stream_factory(identity, local_generation)
        return WindowsGraphicsCaptureStreamV3(
            identity,
            local_generation=local_generation,
            minimum_update_interval_ms=self.minimum_update_interval_ms,
        )

    def select_foreground_source(self) -> bool:
        if not self._selection_lock.acquire(blocking=False):
            active = self.active_snapshot()
            self._set_status(
                "switching" if active is not None else "selecting",
                "A source selection is already open.",
                active=active,
            )
            return False
        candidate: WindowsGraphicsCaptureStreamV3 | None = None
        claimed_binding: RegionBindingV3 | None = None
        operation_epoch = 0
        try:
            with self._lock:
                self._operation_epoch += 1
                operation_epoch = self._operation_epoch
                candidate_generation = self._local_generation + 1
            identity = self.foreground_reader()
            active_before_switch = self.active_snapshot()
            self._set_status(
                "switching" if active_before_switch is not None else "selecting",
                "Preparing the exact WGC frame for region selection.",
                active=active_before_switch,
            )
            candidate = self._new_stream(identity, candidate_generation)
            candidate.start()
            first_frame = candidate.wait_first_frame()
            try:
                normalized_bbox = self.selector(identity, first_frame)
            except Exception as exc:
                with self._lock:
                    selection_was_stopped = operation_epoch != self._operation_epoch
                if selection_was_stopped:
                    raise SourceSelectionCancelled("Source selection was stopped.") from exc
                raise
            current_identity = self.identity_reader(identity.hwnd)
            if not identity.same_target(current_identity):
                raise RuntimeError("The selected window identity changed before the region was confirmed.")
            selection = RegionSelectionV3(
                identity=current_identity,
                normalized_bbox=normalize_region_bbox_v3(normalized_bbox),
                selection_id=uuid4().hex,
                sequence_id=f"wgc-{uuid4().hex}",
                reference_frame_size=first_frame.image.size,
            )
            with self._lock:
                if operation_epoch != self._operation_epoch:
                    raise SourceSelectionCancelled("Source selection was superseded before claim.")
                self._claim_in_progress = True
            try:
                lease = self.ingest_client.claim_source(selection)
                claimed_binding = RegionBindingV3(selection=selection, lease=lease)
            finally:
                with self._lock:
                    self._claim_in_progress = False

            with self._lock:
                if operation_epoch != self._operation_epoch:
                    raise SourceSelectionCancelled("Source selection was stopped before commit.")
                old_active = self._active
                self._local_generation = candidate_generation
                self._active = ActiveRegionSourceV3(
                    local_generation=candidate_generation,
                    binding=claimed_binding,
                    stream=candidate,
                )
            candidate = None
            if old_active is not None:
                old_active.stream.stop()
            self._set_status(
                "validating",
                f"Region locked for {selection.display_name}; waiting for its first accepted frame.",
                active=self._active,
                source_live=False,
            )
            return True
        except SourceSelectionCancelled as exc:
            active = self.active_snapshot()
            message = str(exc) or f"Press {SELECT_HOTKEY} to select a chart region."
            if active is not None:
                message = f"Source switch cancelled; the existing region remains active. {message}"
            active_status = str(active.last_health_status) if active is not None else "ready"
            self._set_status(
                active_status,
                message,
                active=active,
                source_live=active_status == "live",
            )
            return False
        except Exception as exc:
            LOGGER.exception("Windows region source selection failed.")
            active = self.active_snapshot()
            message = str(exc)[:500]
            if active is not None:
                message = f"Source switch failed; the existing region remains active. {message}"[:500]
            active_status = str(active.last_health_status) if active is not None else "error"
            self._set_status(
                active_status,
                message,
                active=active,
                source_live=active_status == "live",
            )
            return False
        finally:
            if candidate is not None:
                candidate.stop()
            if claimed_binding is not None:
                with self._lock:
                    committed = bool(
                        self._active is not None
                        and self._active.binding.lease.source_lease_id
                        == claimed_binding.lease.source_lease_id
                    )
                if not committed:
                    try:
                        self.ingest_client.kill_source(claimed_binding, reason="selection_not_committed")
                    except Exception:
                        LOGGER.debug("Unable to release an uncommitted source lease.", exc_info=True)
            self._selection_lock.release()

    @staticmethod
    def _server_claim_is_safe(
        source: Mapping[str, Any],
        selection: RegionSelectionV3,
        *,
        allow_same_sequence: bool,
    ) -> bool:
        """Fence local recovery against a killed or independently owned source."""

        state = str(source.get("state", "") or "").strip().upper()
        source_id = str(source.get("source_id", "") or "").strip()
        sequence_id = str(source.get("sequence_id", "") or "").strip()
        generation = int(source.get("source_generation", 0) or 0)
        if state == "KILLED":
            return False
        no_owner = bool(
            state == "NO_SOURCE"
            and not source_id
            and not sequence_id
            and generation <= 0
        )
        if no_owner:
            return True
        if not allow_same_sequence:
            return False
        return bool(
            state in {"VALIDATING", "LIVE", "STALE"}
            and source_id == WGC_SOURCE_ID
            and sequence_id == selection.sequence_id
            and str(source.get("source_type", "") or "").strip() == WGC_SOURCE_TYPE
            and str(source.get("coordinate_space", "") or "").strip() == WGC_COORDINATE_SPACE
        )

    @staticmethod
    def _selection_from_public_binding(payload: Mapping[str, Any]) -> RegionSelectionV3:
        row = dict(payload)
        active_source = row.get("active_source")
        if not row.get("source_id") and isinstance(active_source, Mapping):
            row = dict(cast(Mapping[str, Any], active_source))
        if str(row.get("schema_version", "") or "").strip() != WGC_BINDING_SCHEMA:
            raise ValueError("The saved region binding schema is not supported.")
        if str(row.get("source_id", "") or "").strip() != WGC_SOURCE_ID:
            raise ValueError("The saved binding does not belong to Windows region capture.")
        if str(row.get("source_type", "") or "").strip() != WGC_SOURCE_TYPE:
            raise ValueError("The saved binding has the wrong source type.")
        if str(row.get("coordinate_space", "") or "").strip() != WGC_COORDINATE_SPACE:
            raise ValueError("The saved binding has the wrong coordinate space.")

        window_value = row.get("window")
        if not isinstance(window_value, Mapping):
            raise ValueError("The saved region binding has no window identity.")
        window = cast(Mapping[str, Any], window_value)
        rect_values = window.get("window_rect")
        if not isinstance(rect_values, Sequence) or isinstance(rect_values, (str, bytes)):
            raise ValueError("The saved window geometry is invalid.")
        rect: list[Any] = list(cast(Sequence[Any], rect_values))
        if len(rect) < 4:
            raise ValueError("The saved window geometry is invalid.")
        saved_identity = WindowIdentityV3(
            hwnd=int(window.get("hwnd", 0) or 0),
            process_id=int(window.get("process_id", 0) or 0),
            process_create_time=float(window.get("process_create_time", 0.0) or 0.0),
            process_path=str(window.get("process_path", "") or ""),
            class_name=str(window.get("class_name", "") or ""),
            title=str(window.get("title", "") or ""),
            rect=(int(rect[0]), int(rect[1]), int(rect[2]), int(rect[3])),
            is_visible=bool(window.get("is_visible", False)),
            is_minimized=bool(window.get("is_minimized", False)),
        )
        if saved_identity.hwnd <= 0 or saved_identity.process_id <= 0:
            raise ValueError("The saved window identity is incomplete.")
        normalized_bbox = normalize_region_bbox_v3(
            cast(Sequence[Any], row.get("normalized_bbox", []))
        )
        reference_values = row.get("reference_frame_size")
        if (
            not isinstance(reference_values, Sequence)
            or isinstance(reference_values, (str, bytes))
        ):
            raise ValueError("The saved reference frame size is invalid.")
        reference: list[Any] = list(cast(Sequence[Any], reference_values))
        if len(reference) < 2:
            raise ValueError("The saved reference frame size is invalid.")
        reference_size = (int(reference[0]), int(reference[1]))
        if reference_size[0] <= 0 or reference_size[1] <= 0:
            raise ValueError("The saved reference frame size is invalid.")
        selection_id = str(row.get("selection_id", "") or "").strip()
        sequence_id = str(row.get("sequence_id", "") or "").strip()
        if not selection_id or not sequence_id:
            raise ValueError("The saved region binding has no selection sequence.")
        return RegionSelectionV3(
            identity=saved_identity,
            normalized_bbox=normalized_bbox,
            selection_id=selection_id,
            sequence_id=sequence_id,
            reference_frame_size=reference_size,
        )

    def restore_public_binding(self, payload: Mapping[str, Any]) -> bool:
        """Restore the exact saved HWND/ROI only when the server fence permits it."""

        if not self._selection_lock.acquire(blocking=False):
            return False
        candidate: WindowsGraphicsCaptureStreamV3 | None = None
        claimed_binding: RegionBindingV3 | None = None
        operation_epoch = 0
        try:
            saved_selection = self._selection_from_public_binding(payload)
            with self._lock:
                if self._active is not None:
                    raise RuntimeError("A Windows region source is already active.")
                self._operation_epoch += 1
                operation_epoch = self._operation_epoch
                candidate_generation = self._local_generation + 1

            source = self.ingest_client.get_source_control()
            if not self._server_claim_is_safe(
                source,
                saved_selection,
                allow_same_sequence=True,
            ):
                raise RuntimeError("The saved chart cannot be restored because another source owns the session.")

            current_identity = self.identity_reader(saved_selection.identity.hwnd)
            if not saved_selection.identity.same_target(current_identity):
                raise RuntimeError("The saved chart window identity is no longer current.")
            restored_selection = RegionSelectionV3(
                identity=current_identity,
                normalized_bbox=saved_selection.normalized_bbox,
                selection_id=saved_selection.selection_id,
                sequence_id=saved_selection.sequence_id,
                reference_frame_size=saved_selection.reference_frame_size,
            )
            self._set_status(
                "restoring",
                f"Restoring the selected region from {restored_selection.display_name}.",
                source_live=False,
            )
            candidate = self._new_stream(current_identity, candidate_generation)
            candidate.start()
            candidate.wait_first_frame()

            verified_identity = self.identity_reader(saved_selection.identity.hwnd)
            if not saved_selection.identity.same_target(verified_identity):
                raise RuntimeError("The saved chart window identity changed during restore.")
            # Recheck immediately before claim so an owner observed while WGC
            # was starting is never superseded by this recovery path.
            source = self.ingest_client.get_source_control()
            if not self._server_claim_is_safe(
                source,
                restored_selection,
                allow_same_sequence=True,
            ):
                raise RuntimeError("The saved chart cannot be restored because another source owns the session.")
            with self._lock:
                if operation_epoch != self._operation_epoch or self._active is not None:
                    raise SourceSelectionCancelled("Saved source restore was superseded before claim.")
                self._claim_in_progress = True
            try:
                lease = self.ingest_client.claim_source(
                    restored_selection,
                    expected_source_control=source,
                )
                claimed_binding = RegionBindingV3(selection=restored_selection, lease=lease)
            finally:
                with self._lock:
                    self._claim_in_progress = False
            with self._lock:
                if operation_epoch != self._operation_epoch or self._active is not None:
                    raise SourceSelectionCancelled("Saved source restore was superseded before commit.")
                self._local_generation = candidate_generation
                self._active = ActiveRegionSourceV3(
                    local_generation=candidate_generation,
                    binding=claimed_binding,
                    stream=candidate,
                )
            candidate = None
            self._set_status(
                "validating",
                f"Restored region locked for {restored_selection.display_name}; waiting for its first accepted frame.",
                active=self._active,
                source_live=False,
            )
            return True
        except Exception as exc:
            LOGGER.warning("Saved Windows region source was not restored: %s", exc)
            self._set_status("ready", str(exc)[:500], source_live=False)
            return False
        finally:
            if candidate is not None:
                candidate.stop()
            if claimed_binding is not None:
                with self._lock:
                    committed = bool(
                        self._active is not None
                        and self._active.binding.lease.source_lease_id
                        == claimed_binding.lease.source_lease_id
                    )
                if not committed:
                    try:
                        self.ingest_client.kill_source(claimed_binding, reason="restore_not_committed")
                    except Exception:
                        LOGGER.debug("Unable to release an uncommitted restored source lease.", exc_info=True)
            self._selection_lock.release()

    def _reclaim_after_server_reset(self, active: ActiveRegionSourceV3) -> bool:
        """Reclaim the existing stream only after an explicit ownerless reset."""

        source = self.ingest_client.get_source_control()
        if not self._server_claim_is_safe(
            source,
            active.binding.selection,
            allow_same_sequence=False,
        ):
            return False
        current_identity = self.identity_reader(active.binding.selection.identity.hwnd)
        if not active.binding.selection.identity.same_target(current_identity):
            return False
        with self._lock:
            if self._active is not active:
                return False
            operation_epoch = self._operation_epoch
            self._claim_in_progress = True
        claimed_binding: RegionBindingV3 | None = None
        try:
            lease = self.ingest_client.claim_source(
                active.binding.selection,
                expected_source_control=source,
            )
            claimed_binding = RegionBindingV3(selection=active.binding.selection, lease=lease)
            with self._lock:
                if self._active is not active or operation_epoch != self._operation_epoch:
                    return False
                active.binding = claimed_binding
                active.last_uploaded_frame_id = 0
                active.last_uploaded_epoch = 0.0
                active.next_upload_attempt_epoch = 0.0
                active.consecutive_upload_failures = 0
                active.last_health_status = "validating"
            claimed_binding = None
            self._set_status(
                "validating",
                "The API worker restarted; the exact selected region was safely reclaimed.",
                active=active,
                source_live=False,
            )
            return True
        except SourceLeaseLostError:
            # The compare-and-swap lost to another producer. Do not retry with
            # an ordinary claim and never release the competing server owner.
            return False
        finally:
            with self._lock:
                self._claim_in_progress = False
            if claimed_binding is not None:
                try:
                    self.ingest_client.kill_source(claimed_binding, reason="reclaim_not_committed")
                except Exception:
                    LOGGER.debug("Unable to release an uncommitted reclaimed source lease.", exc_info=True)

    def kill_active_source(self, *, reason: str = "operator_hotkey") -> bool:
        cancel_native_region_selector_v3()
        with self._lock:
            self._operation_epoch += 1
            active = self._active
            self._active = None
            self._local_generation += 1
            self._claim_in_progress = False
        if active is None:
            self._set_status("ready", f"No selected source is active. Press {SELECT_HOTKEY} to select one.")
            return False
        active.stream.stop()
        try:
            self.ingest_client.kill_source(active.binding, reason=reason)
        except Exception:
            LOGGER.warning("The local WGC source stopped, but its server lease cleanup failed.", exc_info=True)
        self._set_status("stopped", f"Selected source stopped. Press {SELECT_HOTKEY} to select another.")
        return True

    def active_snapshot(self) -> ActiveRegionSourceV3 | None:
        with self._lock:
            return self._active

    def _restart_exact_stream(
        self,
        active: ActiveRegionSourceV3,
        current_identity: WindowIdentityV3,
    ) -> bool:
        """Rebuild only the WGC session while preserving the HWND/ROI lease."""

        active.last_stream_restart_epoch = time.time()
        candidate = self._new_stream(current_identity, active.local_generation)
        seed_frame_id = getattr(candidate, "seed_frame_id", None)
        if callable(seed_frame_id):
            previous_frame = active.stream.slot.latest()
            seed_frame_id(
                max(
                    active.last_uploaded_frame_id,
                    int(previous_frame.frame_id) if previous_frame is not None else 0,
                )
            )
        try:
            candidate.start()
            candidate.wait_first_frame(timeout=min(12.0, self.freshness_timeout_sec))
            verified_identity = self.identity_reader(active.binding.selection.identity.hwnd)
            if (
                verified_identity.is_minimized
                or not active.binding.selection.identity.same_target(verified_identity)
            ):
                return False
            with self._lock:
                if self._active is not active:
                    return False
                previous = active.stream
                active.stream = candidate
                active.stream_restart_count += 1
                active.last_stream_restart_epoch = time.time()
                active.last_health_status = "recovering_background"
            candidate = None
            previous.stop()
            return True
        finally:
            if candidate is not None:
                candidate.stop()

    def _uploader_loop(self) -> None:
        while not self._stop_evt.wait(0.10):
            with self._lock:
                active = self._active
                claim_in_progress = self._claim_in_progress
            if active is None or claim_in_progress:
                continue
            try:
                now_epoch = time.time()
                current_identity = active.binding.selection.identity
                if now_epoch - active.last_identity_check_epoch >= self.identity_check_interval_sec:
                    try:
                        current_identity = self.identity_reader(active.binding.selection.identity.hwnd)
                    except Exception as exc:
                        self._hard_stop_active(
                            active,
                            f"The selected window identity is no longer verifiable: {exc}",
                            release_server=True,
                        )
                        continue
                    active.last_identity_check_epoch = now_epoch
                    active.last_verified_minimized = bool(current_identity.is_minimized)
                    if not active.binding.selection.identity.same_target(current_identity):
                        self._hard_stop_active(
                            active,
                            "The selected HWND identity changed.",
                            release_server=True,
                        )
                        continue
                if active.last_verified_minimized:
                    if (
                        now_epoch - active.last_background_restore_epoch
                        < self.identity_check_interval_sec
                    ):
                        continue
                    active.last_background_restore_epoch = now_epoch
                    active.background_restore_count += 1
                    restore_succeeded = bool(
                        self.background_restorer(current_identity)
                    )
                    next_status = (
                        "recovering_background" if restore_succeeded else "background_blocked"
                    )
                    active.last_health_status = next_status
                    self._set_status(
                        next_status,
                        (
                            "The selected window was restored behind your current application without taking focus; "
                            "the exact WGC stream is resuming."
                            if restore_succeeded
                            else "The selected window is minimized and Windows has not yet accepted its no-focus background restore."
                        ),
                        active=active,
                        source_live=False,
                    )
                    continue
                frame = active.stream.slot.latest()
                if frame is None or int(frame.frame_id) <= int(active.last_uploaded_frame_id):
                    if active.stream.closed:
                        if (
                            now_epoch - active.last_stream_restart_epoch
                            >= self.freshness_timeout_sec
                            and self._restart_exact_stream(active, current_identity)
                        ):
                            self._set_status(
                                "recovering_background",
                                "The exact HWND capture session was rebuilt without changing the selected chart or taking focus.",
                                active=active,
                                source_live=False,
                            )
                    elif (
                        frame is not None
                        and now_epoch - float(frame.captured_epoch) > self.freshness_timeout_sec
                        and active.last_health_status != "stale"
                    ):
                        if (
                            now_epoch - active.last_stream_restart_epoch
                            >= self.freshness_timeout_sec
                            and self._restart_exact_stream(active, current_identity)
                        ):
                            self._set_status(
                                "recovering_background",
                                "The covered-window WGC stream was refreshed without activating the selected application.",
                                active=active,
                                source_live=False,
                            )
                        else:
                            active.last_health_status = "stale"
                            self._set_status(
                                "stale",
                                "No fresh WGC frame is available; stale pixels were not uploaded.",
                                active=active,
                                last_frame=frame,
                                source_live=False,
                            )
                    continue
                frame_age = now_epoch - float(frame.captured_epoch)
                if frame_age > self.freshness_timeout_sec:
                    if active.last_health_status != "stale":
                        active.last_health_status = "stale"
                        self._set_status(
                            "stale",
                            "No fresh WGC frame is available; stale pixels were not uploaded.",
                            active=active,
                            last_frame=frame,
                            source_live=False,
                        )
                    continue
                if now_epoch < active.next_upload_attempt_epoch:
                    continue
                if now_epoch - float(active.last_uploaded_epoch) < self.upload_interval_sec:
                    continue
                if active.last_full_frame_size != (0, 0) and active.last_full_frame_size != frame.image.size:
                    active.geometry_generation += 1
                active.last_full_frame_size = frame.image.size
                roi = crop_normalized_region_v3(
                    frame.image,
                    active.binding.selection.normalized_bbox,
                )
                self.ingest_client.upload_frame(active, frame, roi)
                active.last_uploaded_frame_id = int(frame.frame_id)
                active.last_uploaded_epoch = time.time()
                active.next_upload_attempt_epoch = 0.0
                active.consecutive_upload_failures = 0
                active.last_health_status = "live"
                self._set_status(
                    "live",
                    f"Streaming selected region from {active.binding.selection.display_name}.",
                    active=active,
                    last_frame=frame,
                )
            except SourceLeaseLostError as exc:
                # A brief API-worker restart may legitimately clear only the
                # server lease. Reclaim the exact local HWND/ROI only through
                # an atomic server-side compare-and-swap from ownerless state.
                try:
                    reclaimed = self._reclaim_after_server_reset(active)
                except Exception as recovery_exc:
                    active.consecutive_upload_failures += 1
                    active.next_upload_attempt_epoch = time.time() + min(
                        15.0,
                        2.0 ** min(3, active.consecutive_upload_failures - 1),
                    )
                    active.last_health_status = "degraded"
                    LOGGER.warning("Unable to verify WGC lease recovery: %s", recovery_exc)
                    self._set_status(
                        "degraded",
                        f"The source lease changed; recovery is waiting for server verification. {recovery_exc}"[:500],
                        active=active,
                        source_live=False,
                    )
                    continue
                if not reclaimed:
                    # Never call the unscoped server kill route after a
                    # rejected lease: another source may be the rightful owner.
                    self._hard_stop_active(active, str(exc), release_server=False)
            except FrameUploadDeferredError as exc:
                active.consecutive_upload_failures += 1
                active.next_upload_attempt_epoch = time.time() + exc.retry_after_sec
                active.last_health_status = "degraded"
                LOGGER.info("Windows region frame upload deferred: %s", exc)
                self._set_status("degraded", str(exc)[:500], active=active)
            except Exception as exc:
                active.consecutive_upload_failures += 1
                retry_delay = min(30.0, 2.0 ** min(4, active.consecutive_upload_failures - 1))
                active.next_upload_attempt_epoch = time.time() + retry_delay
                active.last_health_status = "degraded"
                LOGGER.warning("Windows region frame upload failed: %s", exc)
                self._set_status("degraded", str(exc)[:500], active=active)

    def _hard_stop_active(
        self,
        active: ActiveRegionSourceV3,
        message: str,
        *,
        release_server: bool,
    ) -> None:
        with self._lock:
            if self._active is not active:
                return
            self._active = None
            self._local_generation += 1
            self._operation_epoch += 1
        active.stream.stop()
        if release_server:
            try:
                self.ingest_client.kill_source(active.binding, reason="wgc_source_invalidated")
            except Exception:
                LOGGER.debug("Unable to release the invalidated WGC source lease.", exc_info=True)
        self._set_status("hard_stopped", str(message or "The source lease ended."), source_live=False)

    def _set_status(
        self,
        status: str,
        message: str,
        *,
        active: ActiveRegionSourceV3 | None = None,
        last_frame: CapturedWindowFrameV3 | None = None,
        source_live: bool | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "schema_version": WGC_STATUS_SCHEMA,
            "status": str(status),
            "message": str(message),
            "select_hotkey": SELECT_HOTKEY,
            "kill_hotkey": KILL_HOTKEY,
            "source_live": bool(active is not None) if source_live is None else bool(source_live),
            "hotkey_registered": bool(self._hotkey_registered),
            "hotkey_error": str(self._hotkey_error),
            "updated_epoch": time.time(),
        }
        if active is not None:
            payload["active_source"] = active.binding.public_payload()
            payload["last_uploaded_frame_id"] = int(active.last_uploaded_frame_id)
            payload["last_uploaded_epoch"] = float(active.last_uploaded_epoch)
            payload["geometry_generation"] = int(active.geometry_generation)
            payload["background_capture_mode"] = "exact_hwnd_no_activation"
            payload["background_restore_count"] = int(active.background_restore_count)
            payload["stream_restart_count"] = int(active.stream_restart_count)
            payload["target_minimized"] = bool(active.last_verified_minimized)
        if last_frame is not None:
            payload["last_wgc_frame_epoch"] = float(last_frame.captured_epoch)
            payload["last_wgc_frame_id"] = int(last_frame.frame_id)
        with self._lock:
            self._status = payload
        self._write_status()

    def _write_status(self) -> None:
        with self._lock:
            payload = dict(self._status)
            temporary = self.status_path.with_suffix(
                self.status_path.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                self.status_path.parent.mkdir(parents=True, exist_ok=True)
                temporary.write_text(
                    json.dumps(payload, indent=2, sort_keys=True),
                    encoding="utf-8",
                )
                temporary.replace(self.status_path)
            except OSError:
                LOGGER.debug("Unable to persist Windows region capture status.", exc_info=True)
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def shutdown(self) -> None:
        self._stop_evt.set()
        self.kill_active_source(reason="agent_shutdown")
        thread = self._uploader_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self.ingest_client.close()


class GlobalRegionHotkeyLoopV3:
    """Register global selector/kill hotkeys without a keyboard hook."""

    def __init__(
        self,
        *,
        on_select: Callable[[], Any],
        on_kill: Callable[[], Any],
        on_registration: Callable[[bool, str], Any] | None = None,
    ) -> None:
        self.on_select = on_select
        self.on_kill = on_kill
        self.on_registration = on_registration
        self._thread_id = 0
        self._registered = False

    def run(self) -> None:
        if os.name != "nt":
            raise WgcRuntimeUnavailableError("Global Windows region hotkeys require Windows.")
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        modifiers = _MOD_CONTROL | _MOD_SHIFT | _MOD_NOREPEAT
        if not bool(user32.RegisterHotKey(None, _HOTKEY_SELECT_ID, modifiers, ord("B"))):
            message = f"Unable to register {SELECT_HOTKEY}; another application may own it."
            if self.on_registration is not None:
                self.on_registration(False, message)
            raise RuntimeError(message)
        if not bool(user32.RegisterHotKey(None, _HOTKEY_KILL_ID, modifiers, ord("K"))):
            user32.UnregisterHotKey(None, _HOTKEY_SELECT_ID)
            message = f"Unable to register {KILL_HOTKEY}; another application may own it."
            if self.on_registration is not None:
                self.on_registration(False, message)
            raise RuntimeError(message)
        self._thread_id = int(kernel32.GetCurrentThreadId())
        self._registered = True
        if self.on_registration is not None:
            self.on_registration(True, "")
        message = wintypes.MSG()
        try:
            while int(user32.GetMessageW(ctypes.byref(message), None, 0, 0)) > 0:
                if int(message.message) != _WM_HOTKEY:
                    continue
                hotkey_id = int(message.wParam)
                callback = self.on_select if hotkey_id == _HOTKEY_SELECT_ID else self.on_kill
                threading.Thread(
                    target=callback,
                    name=(
                        "phoenixguard-wgc-select-hotkey"
                        if hotkey_id == _HOTKEY_SELECT_ID
                        else "phoenixguard-wgc-kill-hotkey"
                    ),
                    daemon=True,
                ).start()
        finally:
            user32.UnregisterHotKey(None, _HOTKEY_SELECT_ID)
            user32.UnregisterHotKey(None, _HOTKEY_KILL_ID)
            self._registered = False
            if self.on_registration is not None:
                self.on_registration(False, "Hotkey loop stopped.")

    def stop(self) -> None:
        if not self._registered or self._thread_id <= 0 or os.name != "nt":
            return
        ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
