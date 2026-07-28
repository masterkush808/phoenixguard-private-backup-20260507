from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from pytest import MonkeyPatch
from fastapi.testclient import TestClient
from PIL import Image

from phoenixguard.mobile_api import app as mobile_app_module
from phoenixguard.mobile_api import pipeline as mobile_pipeline_module
from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.pipeline import PhoenixGuardPipelineAdapter
from phoenixguard.mobile_api.service import MobileApiService, MobileJobCapabilityUnavailableError


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (160, 96), color=color).save(buf, format="PNG")
    return buf.getvalue()


class _FakePipelineAdapter:
    def __init__(self, root: Path) -> None:
        self.root = root

    def describe(self) -> dict[str, Any]:
        return {
            "required_uploads": 4,
            "upload_order": [
                {"key": "higher_zoomed_out", "label": "Higher TF / Zoomed Out"},
                {"key": "higher_zoomed_in", "label": "Higher TF / Zoomed In"},
                {"key": "lower_zoomed_out", "label": "Lower TF / Zoomed Out"},
                {"key": "lower_zoomed_in", "label": "Lower TF / Zoomed In"},
            ],
            "timeframe_choices": ["M5", "M15"],
            "overlay_choices": ["history-plus-projection"],
            "council_scope_choices": ["standard"],
            "default_settings": {
                "overlay_mode": "history-plus-projection",
                "min_conf_global": 0.42,
                "min_conf_latest": 0.50,
                "history_depth": 8,
                "label_density": 10,
                "projection_focus": 0.35,
                "debug_depth": 6,
                "fuse_timeframe_overlays": False,
                "higher_timeframe": "M15",
                "lower_timeframe": "M5",
                "council_scope": "standard",
            },
        }

    def job_submission_capability(self) -> dict[str, Any]:
        return {
            "schema_version": "PG_MOBILE_JOB_CAPABILITY_V1",
            "available": True,
            "status": "AVAILABLE",
            "reason": "The test pipeline adapter accepts mobile jobs.",
        }

    def normalize_render_config(self, settings: Mapping[str, Any] | None) -> dict[str, Any]:
        payload = dict(self.describe()["default_settings"])
        payload.update(dict(settings or {}))
        return payload

    def analyze_bundle(
        self,
        upload_paths: Sequence[str],
        render_config: Mapping[str, Any],
    ) -> tuple[dict[str, Any], Any, str]:
        entries: list[dict[str, Any]] = []
        final_upload_path = str(upload_paths[-1]) if upload_paths else ""
        for index, _upload_path in enumerate(upload_paths, start=1):
            raw_path = self.root / f"raw_{index}.png"
            overlay_path = self.root / f"overlay_{index}.png"
            Image.new("RGB", (180, 100), color=(25 * index, 30, 50)).save(raw_path)
            Image.new("RGB", (180, 100), color=(40, 45 * index, 90)).save(overlay_path)
            entries.append(
                {
                    "label": [
                        "Higher TF / Zoomed Out",
                        "Higher TF / Zoomed In",
                        "Lower TF / Zoomed Out",
                        "Lower TF / Zoomed In",
                    ][index - 1],
                    "action": "BUY" if index < 3 else "SELL",
                    "confidence": 0.55 + index * 0.05,
                    "projection_direction": "BUY" if index < 3 else "SELL",
                    "bias_direction": "BUY" if index < 3 else "SELL",
                    "bias_strength": 0.60,
                    "setup": "trend",
                    "timeframe": "M15" if index < 3 else "M5",
                    "momentum_bias": "up" if index < 3 else "down",
                    "raw_asset_path": str(raw_path),
                    "overlay_asset_path": str(overlay_path),
                }
            )
        result: dict[str, Any] = {
            "action": "SELL",
            "headline_action": "SELL",
            "active_trade_state": "SELL_TRUE",
            "directional_intent": "SELL",
            "confidence": 0.83,
            "decision_state": "confirmed",
            "execution_permission": "granted",
            "memory_similarity": 0.47,
            "projection": {"direction": "SELL"},
            "timestamp": "2026-04-11T14:00:00+00:00",
            "multi_timeframe": {
                "aligned": False,
                "gate_state": "watch",
                "summary": "Higher pair buying into lower pair sell pressure.",
                "entries": entries,
            },
        }
        return result, np.zeros((96, 160, 3), dtype=np.uint8), final_upload_path

    def normalize_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        return dict(result)

    def export_artifacts(
        self,
        result: Mapping[str, Any],
        source_image_state: Any,
        artifact_dir: Path,
        job_id: str,
    ) -> list[dict[str, Any]]:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[dict[str, Any]] = []
        for index, entry in enumerate(result["multi_timeframe"]["entries"], start=1):
            for kind in ("raw", "overlay"):
                source_path = Path(str(entry[f"{kind}_asset_path"]))
                target_path = artifact_dir / f"{index:02d}_{kind}.png"
                target_path.write_bytes(source_path.read_bytes())
                artifacts.append(
                    {
                        "name": target_path.name,
                        "kind": kind,
                        "label": f"{entry['label']} {kind.title()}",
                        "slot_index": index,
                        "slot_key": [
                            "higher_zoomed_out",
                            "higher_zoomed_in",
                            "lower_zoomed_out",
                            "lower_zoomed_in",
                        ][index - 1],
                        "slot_label": str(entry["label"]),
                        "path": str(target_path),
                    }
                )
        fusion_path = artifact_dir / "multi_timeframe_fusion.png"
        Image.new("RGB", (200, 120), color=(110, 90, 60)).save(fusion_path)
        artifacts.append(
            {
                "name": fusion_path.name,
                "kind": "fusion",
                "label": "Timeframe Fusion",
                "path": str(fusion_path),
            }
        )
        return artifacts


def test_mobile_api_service_runs_job_and_exposes_result(tmp_path: Path) -> None:
    service = MobileApiService(root_dir=tmp_path / "mobile_api", pipeline_adapter=_FakePipelineAdapter(tmp_path))
    job = service.create_job(
        [
            ("higher_1.png", _png_bytes((40, 50, 60))),
            ("higher_2.png", _png_bytes((50, 60, 70))),
            ("lower_1.png", _png_bytes((60, 70, 80))),
            ("lower_2.png", _png_bytes((70, 80, 90))),
        ],
        settings={"higher_timeframe": "M15", "lower_timeframe": "M5"},
    )
    completed = service.wait_for_job(str(job["job_id"]), timeout=5)
    assert completed["status"] == "completed"
    result = completed["result"]
    assert result["action"] == "SELL"
    assert result["multi_timeframe"]["entries"][0]["label"] == "Higher TF / Zoomed Out"
    assert result["artifacts"][0]["url"].startswith(f"/v1/mobile/jobs/{job['job_id']}/artifacts/")


def test_mobile_api_http_surface_accepts_four_images(tmp_path: Path) -> None:
    service = MobileApiService(root_dir=tmp_path / "mobile_api_http", pipeline_adapter=_FakePipelineAdapter(tmp_path))
    client = TestClient(create_app(service))
    response = client.post(
        "/v1/mobile/jobs",
        files=[
            ("screenshots", ("higher_1.png", _png_bytes((10, 20, 30)), "image/png")),
            ("screenshots", ("higher_2.png", _png_bytes((20, 30, 40)), "image/png")),
            ("screenshots", ("lower_1.png", _png_bytes((30, 40, 50)), "image/png")),
            ("screenshots", ("lower_2.png", _png_bytes((40, 50, 60)), "image/png")),
        ],
        data={"higher_timeframe": "M15", "lower_timeframe": "M5"},
    )
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    service.wait_for_job(job_id, timeout=5)
    job_response = client.get(f"/v1/mobile/jobs/{job_id}")
    assert job_response.status_code == 200
    payload = job_response.json()
    assert payload["status"] == "completed"
    artifact_url = payload["result"]["artifacts"][0]["url"]
    artifact_response = client.get(artifact_url)
    assert artifact_response.status_code == 200


def test_mobile_api_unknown_and_invalid_adapter_capabilities_fail_closed(
    tmp_path: Path,
) -> None:
    class _InvalidCapabilityAdapter:
        def job_submission_capability(self) -> dict[str, Any]:
            return {
                "available": "yes",
                "status": "AVAILABLE",
                "active_environment": ".venv-dev",
            }

    adapters: tuple[Any, ...] = (object(), _InvalidCapabilityAdapter())
    for index, adapter in enumerate(adapters):
        service = MobileApiService(
            root_dir=tmp_path / f"mobile_api_fail_closed_{index}",
            pipeline_adapter=adapter,
        )
        capability = service.job_submission_capability()
        assert capability == {
            "schema_version": "PG_MOBILE_JOB_CAPABILITY_V1",
            "available": False,
            "status": "UNAVAILABLE",
            "reason": "Mobile analysis is unavailable in this workspace.",
        }
        try:
            service.create_job([])
        except MobileJobCapabilityUnavailableError as exc:
            assert exc.capability == capability
        else:
            raise AssertionError("An undeclared pipeline capability must fail closed.")
        assert service.list_jobs() == []


def test_mobile_api_live_profile_exposes_clean_unavailable_capability(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_PYTHON_ENV_NAME", ".venv-live")
    monkeypatch.setenv("PHOENIXGUARD_PYTHON_PROFILE", "live")

    def _reject_frontend_import(name: str, package: str | None = None) -> Any:
        if name == "main":
            raise AssertionError("The live capability/config path must not import the dashboard module.")
        return importlib_import_module(name, package)

    importlib_import_module = mobile_pipeline_module.importlib.import_module
    monkeypatch.setattr(mobile_pipeline_module.importlib, "import_module", _reject_frontend_import)

    service = MobileApiService(
        root_dir=tmp_path / "mobile_api_live",
        pipeline_adapter=PhoenixGuardPipelineAdapter(),
    )
    client = TestClient(create_app(service))

    config_response = client.get("/v1/mobile/config")
    assert config_response.status_code == 200
    capability = config_response.json()["job_submission_capability"]
    assert capability["schema_version"] == "PG_MOBILE_JOB_CAPABILITY_V1"
    assert capability["available"] is False
    assert capability["status"] == "UNAVAILABLE_IN_LIVE_PROFILE"
    assert capability["reason"] == (
        "The legacy four-screenshot analyzer is unavailable in this workspace."
    )
    assert set(capability) == {"schema_version", "available", "status", "reason"}

    job_response = client.post(
        "/v1/mobile/jobs",
        files=[
            ("screenshots", (f"frame_{index}.png", _png_bytes((10 * index, 20, 30)), "image/png"))
            for index in range(1, 5)
        ],
    )
    assert job_response.status_code == 503
    assert job_response.json()["detail"] == capability
    assert service.list_jobs() == []

    malformed_response = client.post("/v1/mobile/jobs")
    assert malformed_response.status_code == 422
    validation_errors = malformed_response.json()["detail"]
    assert any(
        error.get("loc") == ["body", "screenshots"]
        and error.get("type") == "missing"
        for error in validation_errors
    )
    assert service.list_jobs() == []


def test_full_live_state_routes_strip_host_paths_without_losing_public_geometry(
    monkeypatch: MonkeyPatch,
) -> None:
    session_id = "public-host-path-redaction"
    raw_live_state: dict[str, object] = {
        "schema_version": "PG_LIVE_VISUAL_STATE_V3",
        "session_id": session_id,
        "last_chart_path": r"C:\private\runtime\chart.png",
        "workspace_root": r"C:\private\phoenixguard",
        "artifacts": {
            "chart": {
                "path": r"C:\private\runtime\chart.png",
                "exists": True,
                "width": 1280,
                "height": 720,
                "url": (
                    f"/v1/mobile/window-tracker/sessions/{session_id}"
                    "/artifacts/latest-chart?v=27"
                ),
            }
        },
        "broker_surface": {
            "frame": {
                "path": r"C:\private\runtime\window.png",
                "primary_url": (
                    f"/v1/mobile/window-tracker/sessions/{session_id}"
                    "/artifacts/latest-window?frame_id=27"
                ),
            }
        },
        "execution_debug": {
            "log_path": r"C:\private\runtime\decision.jsonl",
            "status": "WAIT",
        },
        "memory_projection_future": {
            "reference_image_path": r"C:\private\memory\reference.png",
            "summary": "Price may retest before continuation.",
        },
        "overlays": {
            "objects": [
                {
                    "id": "forecast-path-27",
                    "path": [[0.4, 0.6], [0.5, 0.45]],
                    "forecast_path": [
                        {"step": 1, "expected_close_norm": 0.45}
                    ],
                    "source_path": "tracking_summary.historical_structure[0]",
                    "artifact_path": r"C:\private\models\forecast.pt",
                }
            ]
        },
    }

    def _build_live_state(
        _tracker: object,
        requested_session_id: str,
        **_kwargs: object,
    ) -> dict[str, object]:
        assert requested_session_id == session_id
        return raw_live_state

    monkeypatch.setattr(
        mobile_app_module,
        "build_live_state_v3_from_tracker_service",
        _build_live_state,
    )

    with TestClient(create_app(window_tracker_service=object())) as client:
        responses = (
            client.get(f"/v1/mobile/live/state/v3/{session_id}"),
            client.get("/v1/mobile/live/state/v3", params={"session_id": session_id}),
        )

    for response in responses:
        assert response.status_code == 200
        payload = response.json()
        assert "last_chart_path" not in payload
        assert "workspace_root" not in payload
        assert payload["artifacts"]["chart"] == {
            "exists": True,
            "width": 1280,
            "height": 720,
            "url": (
                f"/v1/mobile/window-tracker/sessions/{session_id}"
                "/artifacts/latest-chart?v=27"
            ),
        }
        assert payload["broker_surface"]["frame"] == {
            "primary_url": (
                f"/v1/mobile/window-tracker/sessions/{session_id}"
                "/artifacts/latest-window?frame_id=27"
            )
        }
        assert payload["execution_debug"] == {"status": "WAIT"}
        assert "memory_projection_future" not in payload
        assert payload["overlays"]["objects"] == []

    # Response projection must not mutate the runtime's authoritative state.
    assert raw_live_state["last_chart_path"] == r"C:\private\runtime\chart.png"
    assert raw_live_state["workspace_root"] == r"C:\private\phoenixguard"


def test_mobile_api_live_capability_blocks_direct_service_submission(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_PYTHON_ENV_NAME", ".venv-live")
    service = MobileApiService(
        root_dir=tmp_path / "mobile_api_live_direct",
        pipeline_adapter=PhoenixGuardPipelineAdapter(),
    )

    try:
        service.create_job([])
    except MobileJobCapabilityUnavailableError as exc:
        assert exc.capability["status"] == "UNAVAILABLE_IN_LIVE_PROFILE"
    else:
        raise AssertionError("The live compatibility adapter must fail closed before staging a job.")


def test_mobile_api_development_profile_reports_job_submission_available(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("PHOENIXGUARD_PYTHON_ENV_NAME", ".venv-dev")
    monkeypatch.setenv("PHOENIXGUARD_PYTHON_PROFILE", "dev")

    capability = PhoenixGuardPipelineAdapter().job_submission_capability()

    assert capability["available"] is True
    assert capability["status"] == "AVAILABLE"
    assert capability["reason"] == "The four-screenshot analyzer is available."
    assert set(capability) == {"schema_version", "available", "status", "reason"}


def test_mobile_api_health_route_is_lazy(monkeypatch: MonkeyPatch) -> None:
    def _boom() -> None:
        raise AssertionError("default services should not initialize on /v1/mobile/health")

    monkeypatch.setattr(mobile_app_module, "_service", _boom)
    monkeypatch.setattr(mobile_app_module, "_observer_service", _boom)
    monkeypatch.setattr(mobile_app_module, "_window_tracker_service", _boom)

    client = TestClient(create_app())
    response = client.get("/v1/mobile/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
