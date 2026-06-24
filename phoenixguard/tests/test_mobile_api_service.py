from __future__ import annotations

import io
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
from pytest import MonkeyPatch
from fastapi.testclient import TestClient
from PIL import Image

from phoenixguard.mobile_api import app as mobile_app_module
from phoenixguard.mobile_api.app import create_app
from phoenixguard.mobile_api.service import MobileApiService


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
