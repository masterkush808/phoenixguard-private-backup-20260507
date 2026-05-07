# pyright: reportUnusedFunction=none
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Mapping, cast

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from phoenixguard.core.config import RUNTIME, VOICE, VoiceConfig
from phoenixguard.tracing import configure_tracing, instrument_fastapi_app
from phoenixguard.voice.control import (
    apply_voice_preferences,
    execute_voice_command,
    get_voice_runtime_snapshot,
    update_voice_state,
)
from phoenixguard.voice.intents import public_voice_command_catalog
from phoenixguard.voice.live import (
    LocalWindowTrackerVoiceController,
    build_market_context_from_tracker_session,
)

from .observer import SignalObserverService
from .service import MobileApiService
from .window_tracker import ContinuousWindowTrackerService


_default_service: MobileApiService | None = None
_default_observer_service: SignalObserverService | None = None
_default_window_tracker_service: ContinuousWindowTrackerService | None = None
_WINDOW_TRACKER_DASHBOARD_TEMPLATE = (
    Path(__file__).resolve().parent / "static" / "window_tracker_dashboard.html"
)
_WINDOW_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC = 3.0
_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC = 0.5
_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC = 10.0
_WINDOW_TRACKER_BRAND_ASSET_DIR = (
    Path(__file__).resolve().parents[2] / "assets" / "share" / "css-control"
)
_WINDOW_TRACKER_BRAND_ASSETS = frozenset(
    {
        "landing-transition-lifestyle-suite.png",
        "landing-transition-lifestyle-travel.png",
        "landing-transition-market-vision-alt.png",
        "landing-transition-market-vision.png",
    }
)
_DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID = "pocket-live-8788"


def _service() -> MobileApiService:
    global _default_service
    if _default_service is None:
        _default_service = MobileApiService()
    return _default_service


def _observer_service(mobile_service: MobileApiService | None = None) -> SignalObserverService:
    global _default_observer_service
    if _default_observer_service is None:
        service = mobile_service or _service()
        root_dir = Path(getattr(service, "root_dir", RUNTIME.data_dir / "mobile_api")) / "observer"
        _default_observer_service = SignalObserverService(
            root_dir=root_dir,
            pipeline_adapter=service.pipeline_adapter,
        )
    return _default_observer_service


def _window_tracker_service(
    observer_service: SignalObserverService | None = None,
    mobile_service: MobileApiService | None = None,
) -> ContinuousWindowTrackerService:
    global _default_window_tracker_service
    if _default_window_tracker_service is None:
        service = mobile_service or _service()
        observer = observer_service or _observer_service(service)
        root_dir = Path(getattr(service, "root_dir", RUNTIME.data_dir / "mobile_api")) / "window_tracker"
        _default_window_tracker_service = ContinuousWindowTrackerService(
            observer_service=observer,
            root_dir=root_dir,
        )
    return _default_window_tracker_service


class ObserverSessionCreateRequest(BaseModel):
    session_id: str | None = None
    name: str = ""
    market: str = ""
    settings: dict[str, object] = Field(default_factory=dict)
    policy: dict[str, object] = Field(default_factory=dict)


class WindowTrackerSessionCreateRequest(BaseModel):
    session_id: str | None = None
    name: str = ""
    market: str = ""
    window_query: str = "Pocket Option"
    layout_profile: str = "auto"
    capture_interval_sec: float = Field(
        default=_WINDOW_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC,
        ge=_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC,
        le=_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC,
    )
    rl_track_interval_sec: float = 30.0
    auto_start: bool = False
    observer_settings: dict[str, object] = Field(default_factory=dict)
    observer_policy: dict[str, object] = Field(default_factory=dict)


class WindowTrackerFocusRegionRequest(BaseModel):
    normalized_bbox: list[float] = Field(min_length=4, max_length=4)
    source: str = "dashboard_ctrl_v"


class WindowTrackerControlUpdateRequest(BaseModel):
    capture_interval_sec: float | None = Field(
        default=None,
        ge=_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC,
        le=_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC,
    )
    live_execution_enabled: bool | None = None
    execution_mode: str | None = None
    allow_countertrend_scalp: bool | None = None
    scenario_generation_enabled: bool | None = None
    auto_memory_projection: bool | None = None
    require_memory_projection: bool | None = None
    require_market_identity: bool | None = None
    require_timeframe_identity: bool | None = None
    adaptive_timer_enabled: bool | None = None
    min_capture_interval_sec: float | None = Field(
        default=None,
        ge=_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC,
        le=_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC,
    )
    max_capture_interval_sec: float | None = Field(
        default=None,
        ge=_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC,
        le=_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC,
    )
    max_executions_per_window: int | None = Field(default=None, ge=1, le=20)
    execution_window_sec: float | None = Field(default=None, ge=60.0, le=3600.0)
    min_market_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    min_timeframe_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    cooldown_sec: float | None = Field(default=None, ge=5.0)


class WindowTrackerDemoTradeRequest(BaseModel):
    side: str | None = None
    expiry_seconds: int = Field(default=180, ge=60, le=3600)


class VoicePreferenceUpdateRequest(BaseModel):
    voice_enabled: bool
    listening_enabled: bool
    automatic_timer_enabled: bool
    tracker_capture_interval_sec: float = Field(
        ge=_WINDOW_TRACKER_MIN_CAPTURE_INTERVAL_SEC,
        le=_WINDOW_TRACKER_MAX_CAPTURE_INTERVAL_SEC,
    )
    timezone_name: str = ""
    tracker_session_id: str | None = None


class VoiceCommandRequest(BaseModel):
    command: str = Field(min_length=1)
    tracker_session_id: str | None = None


def _render_window_tracker_dashboard(session_id: str) -> str:
    template = _WINDOW_TRACKER_DASHBOARD_TEMPLATE.read_text(encoding="utf-8")
    return (
        template.replace("__SESSION_ID_JSON__", json.dumps(str(session_id)))
        .replace("__SESSION_LABEL__", str(session_id))
    )


def create_app(
    service: MobileApiService | None = None,
    observer_service: SignalObserverService | None = None,
    window_tracker_service: ContinuousWindowTrackerService | None = None,
    voice_config: VoiceConfig | None = None,
) -> FastAPI:
    resolved_voice_config = voice_config or VOICE
    configure_tracing("phoenixguard-mobile-api", service_version="1.0.0")
    app = FastAPI(
        title="PhoenixGuard Mobile API",
        version="1.0.0",
        summary="Android-facing quartet analysis API and continuous observer surface for PhoenixGuard.",
    )
    app.state.mobile_service = service
    app.state.observer_service = observer_service
    app.state.window_tracker_service = window_tracker_service
    app.state.voice_config = resolved_voice_config

    def get_mobile_service() -> MobileApiService:
        mobile_service = getattr(app.state, "mobile_service", None)
        if mobile_service is None:
            mobile_service = _service()
            app.state.mobile_service = mobile_service
        return mobile_service

    def get_observer_service() -> SignalObserverService:
        market_observer = getattr(app.state, "observer_service", None)
        if market_observer is None:
            mobile_service = get_mobile_service()
            if service is not None:
                market_observer = SignalObserverService(
                    root_dir=Path(mobile_service.root_dir) / "observer",
                    pipeline_adapter=mobile_service.pipeline_adapter,
                )
            else:
                market_observer = _observer_service(mobile_service)
            app.state.observer_service = market_observer
        return market_observer

    def get_window_tracker_service() -> ContinuousWindowTrackerService:
        market_window_tracker = getattr(app.state, "window_tracker_service", None)
        if market_window_tracker is None:
            mobile_service = get_mobile_service()
            market_observer = get_observer_service()
            if service is not None or observer_service is not None:
                market_window_tracker = ContinuousWindowTrackerService(
                    observer_service=market_observer,
                    root_dir=Path(mobile_service.root_dir) / "window_tracker",
                )
            else:
                market_window_tracker = _window_tracker_service(market_observer, mobile_service)
            app.state.window_tracker_service = market_window_tracker
        return market_window_tracker

    def get_voice_config() -> VoiceConfig:
        return getattr(app.state, "voice_config", resolved_voice_config)

    def ensure_window_tracker_dashboard_session(session_id: str) -> dict[str, object]:
        tracker_service = get_window_tracker_service()
        normalized_session_id = str(session_id or "").strip() or _DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID
        try:
            return tracker_service.get_session(normalized_session_id)
        except KeyError:
            return tracker_service.create_session(
                session_id=normalized_session_id,
                name=normalized_session_id,
                market="",
                window_query="Pocket Option",
                layout_profile="auto",
                capture_interval_sec=_WINDOW_TRACKER_DEFAULT_CAPTURE_INTERVAL_SEC,
                rl_track_interval_sec=30.0,
                auto_start=False,
                observer_settings={},
                observer_policy={
                    "single_surface_mode": True,
                    "min_actionable_confidence": 0.58,
                    "min_thesis_confidence": 0.46,
                    "signal_cooldown_sec": 8.0,
                },
            )

    def resolve_window_tracker_dashboard_session_id(session_id: str | None = None) -> str:
        tracker_service = get_window_tracker_service()
        normalized_session_id = str(session_id or "").strip()
        if normalized_session_id:
            payload = ensure_window_tracker_dashboard_session(normalized_session_id)
            return str(payload.get("session_id", normalized_session_id) or normalized_session_id)
        sessions = tracker_service.list_sessions(limit=1)
        if sessions:
            latest_session_id = str(sessions[0].get("session_id", "") or "").strip()
            if latest_session_id:
                return latest_session_id
        payload = ensure_window_tracker_dashboard_session(_DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID)
        return str(payload.get("session_id", _DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID) or _DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID)

    def resolve_voice_tracker_session_id(session_id: str | None = None) -> str:
        snapshot = get_voice_runtime_snapshot(config=get_voice_config())
        requested = str(session_id or snapshot.get("tracker_session_id", "") or "").strip()
        resolved = resolve_window_tracker_dashboard_session_id(
            requested or _DEFAULT_WINDOW_TRACKER_DASHBOARD_SESSION_ID
        )
        if str(snapshot.get("tracker_session_id", "") or "").strip() != resolved:
            update_voice_state(config=get_voice_config(), tracker_session_id=resolved)
        return resolved

    def get_voice_context_payload(tracker_session_id: str | None = None) -> tuple[dict[str, object], dict[str, str]]:
        resolved_session_id = resolve_voice_tracker_session_id(tracker_session_id)
        tracker_session = get_window_tracker_service().get_session(resolved_session_id)
        market_context = build_market_context_from_tracker_session(tracker_session)
        return tracker_session, market_context

    @app.get("/v1/mobile/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/mobile/config")
    def config() -> dict[str, object]:
        return get_mobile_service().describe()

    @app.get("/v1/mobile/jobs")
    def list_jobs(limit: int = 12) -> dict[str, object]:
        return {"jobs": get_mobile_service().list_jobs(limit=limit)}

    @app.get("/v1/mobile/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, object]:
        try:
            return get_mobile_service().get_job(job_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc

    @app.get("/v1/mobile/jobs/{job_id}/artifacts/{artifact_name}")
    def get_artifact(job_id: str, artifact_name: str) -> FileResponse:
        try:
            path = get_mobile_service().artifact_path(job_id, artifact_name)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Job not found.") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Artifact not found.") from exc
        return FileResponse(path)

    @app.post("/v1/mobile/jobs", status_code=status.HTTP_202_ACCEPTED)
    async def create_job(
        screenshots: Annotated[list[UploadFile], File(description="Exactly four ordered screenshots.")],
        overlay_mode: Annotated[str, Form()] = "history-plus-projection",
        min_conf_global: Annotated[float, Form()] = 0.42,
        min_conf_latest: Annotated[float, Form()] = 0.50,
        history_depth: Annotated[int, Form()] = 8,
        label_density: Annotated[int, Form()] = 10,
        projection_focus: Annotated[float, Form()] = 0.35,
        debug_depth: Annotated[int, Form()] = 6,
        fuse_timeframe_overlays: Annotated[bool, Form()] = False,
        higher_timeframe: Annotated[str, Form()] = "M15",
        lower_timeframe: Annotated[str, Form()] = "M5",
        council_scope: Annotated[str, Form()] = "standard",
    ) -> dict[str, object]:
        try:
            uploads = [(upload.filename or f"frame_{index + 1}.png", await upload.read()) for index, upload in enumerate(screenshots)]
            return get_mobile_service().create_job(
                uploads,
                settings={
                    "overlay_mode": overlay_mode,
                    "min_conf_global": min_conf_global,
                    "min_conf_latest": min_conf_latest,
                    "history_depth": history_depth,
                    "label_density": label_density,
                    "projection_focus": projection_focus,
                    "debug_depth": debug_depth,
                    "fuse_timeframe_overlays": fuse_timeframe_overlays,
                    "higher_timeframe": higher_timeframe,
                    "lower_timeframe": lower_timeframe,
                    "council_scope": council_scope,
                },
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/mobile/observer/config")
    def observer_config() -> dict[str, object]:
        return get_observer_service().describe()

    @app.get("/v1/mobile/observer/sessions")
    def list_observer_sessions(limit: int = 20) -> dict[str, object]:
        return {"sessions": get_observer_service().list_sessions(limit=limit)}

    @app.post("/v1/mobile/observer/sessions", status_code=status.HTTP_201_CREATED)
    def create_observer_session(request: ObserverSessionCreateRequest) -> dict[str, object]:
        try:
            return get_observer_service().create_session(
                session_id=request.session_id,
                name=request.name,
                market=request.market,
                settings=request.settings,
                policy=request.policy,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/mobile/observer/sessions/{session_id}")
    def get_observer_session(session_id: str) -> dict[str, object]:
        try:
            return get_observer_service().get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer session not found.") from exc

    @app.get("/v1/mobile/observer/sessions/{session_id}/signals/latest")
    def get_observer_latest_signal(session_id: str) -> dict[str, object]:
        try:
            return get_observer_service().latest_signal(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer session not found.") from exc

    @app.get("/v1/mobile/observer/sessions/{session_id}/bundles/{bundle_id}")
    def get_observer_bundle(session_id: str, bundle_id: str) -> dict[str, object]:
        try:
            return get_observer_service().get_bundle(session_id, bundle_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer bundle not found.") from exc

    @app.get("/v1/mobile/observer/sessions/{session_id}/bundles/{bundle_id}/artifacts/{artifact_name}")
    def get_observer_artifact(session_id: str, bundle_id: str, artifact_name: str) -> FileResponse:
        try:
            path = get_observer_service().artifact_path(session_id, bundle_id, artifact_name)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer bundle not found.") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer artifact not found.") from exc
        return FileResponse(path)

    @app.post("/v1/mobile/observer/sessions/{session_id}/bundles", status_code=status.HTTP_202_ACCEPTED)
    async def submit_observer_bundle(
        session_id: str,
        screenshots: Annotated[list[UploadFile], File(description="Exactly four ordered screenshots.")],
        overlay_mode: Annotated[str, Form()] = "history-plus-projection",
        min_conf_global: Annotated[float, Form()] = 0.42,
        min_conf_latest: Annotated[float, Form()] = 0.50,
        history_depth: Annotated[int, Form()] = 8,
        label_density: Annotated[int, Form()] = 10,
        projection_focus: Annotated[float, Form()] = 0.35,
        debug_depth: Annotated[int, Form()] = 6,
        fuse_timeframe_overlays: Annotated[bool, Form()] = False,
        higher_timeframe: Annotated[str, Form()] = "M15",
        lower_timeframe: Annotated[str, Form()] = "M5",
        council_scope: Annotated[str, Form()] = "standard",
    ) -> dict[str, object]:
        try:
            uploads = [(upload.filename or f"frame_{index + 1}.png", await upload.read()) for index, upload in enumerate(screenshots)]
            return get_observer_service().submit_bundle(
                session_id,
                uploads,
                settings={
                    "overlay_mode": overlay_mode,
                    "min_conf_global": min_conf_global,
                    "min_conf_latest": min_conf_latest,
                    "history_depth": history_depth,
                    "label_density": label_density,
                    "projection_focus": projection_focus,
                    "debug_depth": debug_depth,
                    "fuse_timeframe_overlays": fuse_timeframe_overlays,
                    "higher_timeframe": higher_timeframe,
                    "lower_timeframe": lower_timeframe,
                    "council_scope": council_scope,
                },
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Observer session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/mobile/window-tracker/windows")
    def list_tracker_windows(query: str = "Pocket Option") -> dict[str, object]:
        return {"windows": get_window_tracker_service().list_windows(query)}

    @app.get("/v1/mobile/window-tracker/sessions")
    def list_tracker_sessions(limit: int = 20) -> dict[str, object]:
        return {"sessions": get_window_tracker_service().list_sessions(limit=limit)}

    @app.post("/v1/mobile/window-tracker/sessions", status_code=status.HTTP_201_CREATED)
    def create_tracker_session(request: WindowTrackerSessionCreateRequest) -> dict[str, object]:
        try:
            return get_window_tracker_service().create_session(
                session_id=request.session_id,
                name=request.name,
                market=request.market,
                window_query=request.window_query,
                layout_profile=request.layout_profile,
                capture_interval_sec=request.capture_interval_sec,
                rl_track_interval_sec=request.rl_track_interval_sec,
                auto_start=request.auto_start,
                observer_settings=request.observer_settings,
                observer_policy=request.observer_policy,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}")
    def get_tracker_session(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().get_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.put("/v1/mobile/window-tracker/sessions/{session_id}/focus-region")
    def set_tracker_focus_region(
        session_id: str,
        request: WindowTrackerFocusRegionRequest,
    ) -> dict[str, object]:
        try:
            return get_window_tracker_service().set_focus_region(
                session_id,
                request.normalized_bbox,
                source=request.source,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.delete("/v1/mobile/window-tracker/sessions/{session_id}/focus-region")
    def clear_tracker_focus_region(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().clear_focus_region(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/focus-region/arm")
    def arm_tracker_focus_region(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().arm_focus_selector(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/focus-region/cancel")
    def cancel_tracker_focus_region(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().cancel_focus_selector(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-chart")
    def get_tracker_latest_chart(session_id: str) -> FileResponse:
        try:
            path = get_window_tracker_service().latest_artifact_path(session_id, "chart")
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return FileResponse(path, media_type="image/png")

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-window")
    def get_tracker_latest_window(session_id: str) -> FileResponse:
        try:
            path = get_window_tracker_service().latest_artifact_path(session_id, "window")
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return FileResponse(path, media_type="image/png")

    @app.get("/v1/mobile/window-tracker/sessions/{session_id}/artifacts/latest-{artifact_kind}")
    def get_tracker_latest_named_artifact(session_id: str, artifact_kind: str) -> FileResponse:
        try:
            path = get_window_tracker_service().latest_artifact_path(session_id, artifact_kind)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except FileNotFoundError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        media_type = "image/png" if path.suffix.lower() == ".png" else None
        return FileResponse(path, media_type=media_type)

    @app.get("/v1/mobile/window-tracker/assets/{asset_name}")
    def get_window_tracker_dashboard_asset(asset_name: str) -> FileResponse:
        if asset_name not in _WINDOW_TRACKER_BRAND_ASSETS:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.")
        path = _WINDOW_TRACKER_BRAND_ASSET_DIR / asset_name
        if not path.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dashboard asset not found.")
        return FileResponse(path, media_type="image/png")

    @app.get("/v1/mobile/window-tracker/dashboard", response_class=HTMLResponse)
    def window_tracker_dashboard_default() -> HTMLResponse:
        session_id = resolve_window_tracker_dashboard_session_id()
        return HTMLResponse(_render_window_tracker_dashboard(session_id))

    @app.get("/v1/mobile/window-tracker/dashboard/{session_id}", response_class=HTMLResponse)
    def window_tracker_dashboard(session_id: str) -> HTMLResponse:
        resolved_session_id = resolve_window_tracker_dashboard_session_id(session_id)
        return HTMLResponse(_render_window_tracker_dashboard(resolved_session_id))

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/start")
    def start_tracker_session(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().start_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/stop")
    def stop_tracker_session(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().stop_session(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/emergency-stop")
    def emergency_stop_tracker_session(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().emergency_stop_session(
                session_id,
                reason="Emergency stop requested from dashboard/API.",
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/capture-once")
    def capture_tracker_session_once(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().capture_once(session_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/demo-random-trade")
    def execute_tracker_demo_random_trade(
        session_id: str,
        request: WindowTrackerDemoTradeRequest | None = None,
    ) -> dict[str, object]:
        try:
            payload = request or WindowTrackerDemoTradeRequest()
            return get_window_tracker_service().execute_demo_random_trade(
                session_id,
                side=payload.side,
                expiry_seconds=payload.expiry_seconds,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/predict")
    def predict_tracker_session_from_memory(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().run_memory_projection(session_id, mode="predict")
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.post("/v1/mobile/window-tracker/sessions/{session_id}/show-future")
    def show_future_tracker_session_from_memory(session_id: str) -> dict[str, object]:
        try:
            return get_window_tracker_service().run_memory_projection(session_id, mode="future")
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.patch("/v1/mobile/window-tracker/sessions/{session_id}/controls")
    def update_tracker_session_controls(
        session_id: str,
        request: WindowTrackerControlUpdateRequest,
    ) -> dict[str, object]:
        try:
            return get_window_tracker_service().update_session_controls(
                session_id,
                capture_interval_sec=request.capture_interval_sec,
                live_execution_enabled=request.live_execution_enabled,
                execution_mode=request.execution_mode,
                allow_countertrend_scalp=request.allow_countertrend_scalp,
                scenario_generation_enabled=request.scenario_generation_enabled,
                auto_memory_projection=request.auto_memory_projection,
                require_memory_projection=request.require_memory_projection,
                require_market_identity=request.require_market_identity,
                require_timeframe_identity=request.require_timeframe_identity,
                adaptive_timer_enabled=request.adaptive_timer_enabled,
                min_capture_interval_sec=request.min_capture_interval_sec,
                max_capture_interval_sec=request.max_capture_interval_sec,
                max_executions_per_window=request.max_executions_per_window,
                execution_window_sec=request.execution_window_sec,
                min_market_confidence=request.min_market_confidence,
                min_timeframe_confidence=request.min_timeframe_confidence,
                cooldown_sec=request.cooldown_sec,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Window tracker session not found.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    @app.get("/v1/voice/status")
    def voice_status(tracker_session_id: str | None = None) -> dict[str, object]:
        tracker_session, market_context = get_voice_context_payload(tracker_session_id)
        return {
            "snapshot": get_voice_runtime_snapshot(config=get_voice_config()),
            "market_context": market_context,
            "tracker_session": tracker_session,
            "commands": public_voice_command_catalog(),
        }

    @app.get("/v1/voice/commands")
    def voice_commands() -> dict[str, object]:
        return {"commands": public_voice_command_catalog()}

    @app.post("/v1/voice/preferences")
    def voice_preferences(request: VoicePreferenceUpdateRequest) -> dict[str, object]:
        resolved_session_id = resolve_voice_tracker_session_id(request.tracker_session_id)
        update_voice_state(config=get_voice_config(), tracker_session_id=resolved_session_id)
        tracker_controller = LocalWindowTrackerVoiceController(get_window_tracker_service())
        snapshot = apply_voice_preferences(
            voice_enabled=bool(request.voice_enabled),
            listening_enabled=bool(request.listening_enabled),
            automatic_timer_enabled=bool(request.automatic_timer_enabled),
            tracker_capture_interval_sec=float(request.tracker_capture_interval_sec),
            timezone_name=str(request.timezone_name or ""),
            config=get_voice_config(),
            tracker_controller=tracker_controller,
        )
        tracker_session, market_context = get_voice_context_payload(resolved_session_id)
        return {
            "snapshot": snapshot,
            "market_context": market_context,
            "tracker_session": tracker_session,
        }

    @app.post("/v1/voice/command")
    def voice_command(request: VoiceCommandRequest) -> dict[str, object]:
        resolved_session_id = resolve_voice_tracker_session_id(request.tracker_session_id)
        tracker_session, market_context = get_voice_context_payload(resolved_session_id)
        tracker_controller = LocalWindowTrackerVoiceController(get_window_tracker_service())
        execution = execute_voice_command(
            request.command,
            market_context=market_context,
            config=get_voice_config(),
            tracker_controller=tracker_controller,
        )
        tracker_session, refreshed_market_context = get_voice_context_payload(resolved_session_id)
        match = execution["match"]
        payload = execution.get("payload", {})
        tracker_session_payload = dict(cast(Mapping[str, object], tracker_session))
        execution_payload: object = (
            dict(cast(Mapping[str, object], payload)) if isinstance(payload, Mapping) else payload
        )
        return {
            "response_text": str(execution.get("response_text", "") or ""),
            "match": {
                "name": str(match.name),
                "confidence": float(match.confidence),
                "slots": dict(cast(Mapping[str, object], match.slots)),
                "blocked_sensitive_request": bool(match.blocked_sensitive_request),
            },
            "snapshot": dict(execution.get("snapshot", get_voice_runtime_snapshot(config=get_voice_config()))),
            "market_context": refreshed_market_context,
            "tracker_session": tracker_session_payload,
            "payload": execution_payload,
        }

    instrument_fastapi_app(app)
    return app
