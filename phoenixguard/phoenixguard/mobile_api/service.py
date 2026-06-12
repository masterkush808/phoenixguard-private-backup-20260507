from __future__ import annotations

import io
import json
import logging
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping, Sequence, cast
from uuid import uuid4

from PIL import Image, ImageOps

from phoenixguard.core.config import RUNTIME
from phoenixguard.core.utils import utc_now_iso

from .pipeline import DEFAULT_UPLOAD_ORDER, PhoenixGuardPipelineAdapter, PipelineAdapter


LOGGER = logging.getLogger("phoenixguard.mobile_api.service")

INVALID_UPLOAD_MESSAGE = "Upload exactly four chart images: two higher timeframe views first, then two lower timeframe views."
DEFAULT_MAX_UPLOAD_BYTES = 12 * 1024 * 1024
DEFAULT_MAX_IMAGE_DIMENSION = 8192
DEFAULT_MIN_IMAGE_DIMENSION = 64
ALLOWED_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(dict(payload), ensure_ascii=True, indent=2, default=str), encoding="utf-8")
    for attempt in range(6):
        try:
            tmp_path.replace(path)
            return
        except PermissionError:
            if attempt >= 5:
                raise
            time.sleep(0.05 * float(attempt + 1))


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _payload_dict(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, Any], value))
    return {}


def _payload_items(value: object) -> list[Mapping[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    items = cast(Sequence[object], value)
    return [cast(Mapping[str, Any], item) for item in items if isinstance(item, Mapping)]


class MobileApiService:
    def __init__(
        self,
        *,
        root_dir: Path | None = None,
        pipeline_adapter: PipelineAdapter | None = None,
        max_workers: int = 1,
        max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES,
        max_image_dimension: int = DEFAULT_MAX_IMAGE_DIMENSION,
        min_image_dimension: int = DEFAULT_MIN_IMAGE_DIMENSION,
    ) -> None:
        self.root_dir = Path(root_dir or (RUNTIME.data_dir / "mobile_api"))
        self.jobs_dir = self.root_dir / "jobs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.pipeline_adapter: PipelineAdapter = pipeline_adapter or PhoenixGuardPipelineAdapter()
        self.max_upload_bytes = int(max_upload_bytes)
        self.max_image_dimension = int(max_image_dimension)
        self.min_image_dimension = int(min_image_dimension)
        self._executor = ThreadPoolExecutor(max_workers=max(1, int(max_workers)), thread_name_prefix="phoenixguard-mobile")
        self._lock = threading.RLock()
        self._futures: dict[str, Future[Any]] = {}

    def describe(self) -> dict[str, Any]:
        pipeline = dict(self.pipeline_adapter.describe())
        return {
            "product": {
                "name": "PhoenixGuard Mobile",
                "subtitle": "Premium Quartet Desk",
                "mood": "obsidian-metal",
                "theme": {
                    "background": "#05070A",
                    "surface": "#0D1218",
                    "surface_alt": "#131A22",
                    "accent": "#D6A668",
                    "accent_soft": "#F0D0A3",
                    "text": "#F4EBDD",
                    "muted": "#8A96A3",
                },
            },
            "pipeline": pipeline,
            "limits": {
                "max_upload_bytes": self.max_upload_bytes,
                "min_dimension": self.min_image_dimension,
                "max_dimension": self.max_image_dimension,
            },
        }

    def create_job(self, uploads: Sequence[tuple[str, bytes]], settings: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if len(uploads) != len(DEFAULT_UPLOAD_ORDER):
            raise ValueError(INVALID_UPLOAD_MESSAGE)
        render_config = self.pipeline_adapter.normalize_render_config(settings or {})
        job_id = uuid4().hex
        job_dir = self.jobs_dir / job_id
        upload_records = self._stage_uploads(job_dir / "uploads", uploads)
        job_payload: dict[str, Any] = {
            "job_id": job_id,
            "status": "queued",
            "created_at": utc_now_iso(),
            "updated_at": utc_now_iso(),
            "started_at": "",
            "completed_at": "",
            "last_error": "",
            "settings": render_config,
            "uploads": upload_records,
            "upload_order": [dict(slot) for slot in DEFAULT_UPLOAD_ORDER],
            "result_path": "",
            "artifacts": [],
        }
        self._write_job(job_id, job_payload)
        future = self._executor.submit(self._run_job, job_id)
        with self._lock:
            self._futures[job_id] = future
        return self.get_job(job_id)

    def get_job(self, job_id: str) -> dict[str, Any]:
        payload = self._read_job(job_id)
        if not payload:
            raise KeyError(job_id)
        public_payload = self._public_job_payload(payload)
        result_path = str(payload.get("result_path", "")).strip()
        if result_path and Path(result_path).exists():
            public_payload["result"] = _read_json(Path(result_path), {})
        return public_payload

    def list_jobs(self, limit: int = 12) -> list[dict[str, Any]]:
        job_payloads: list[dict[str, Any]] = []
        for path in sorted(self.jobs_dir.glob("*/job.json"), reverse=True):
            payload = _payload_dict(_read_json(path, {}))
            if payload:
                job_payloads.append(self._public_job_payload(payload))
        return job_payloads[: max(1, int(limit))]

    def artifact_path(self, job_id: str, artifact_name: str) -> Path:
        payload = self._read_job(job_id)
        if not payload:
            raise KeyError(job_id)
        safe_name = Path(str(artifact_name)).name
        for artifact in _payload_items(payload.get("artifacts", [])):
            if str(artifact.get("name", "")) == safe_name:
                path = Path(str(artifact.get("path", "")))
                if path.exists() and path.is_file():
                    return path
                break
        raise FileNotFoundError(safe_name)

    def wait_for_job(self, job_id: str, timeout: float | None = None) -> dict[str, Any]:
        with self._lock:
            future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)
        return self.get_job(job_id)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=False)

    def _stage_uploads(self, upload_dir: Path, uploads: Sequence[tuple[str, bytes]]) -> list[dict[str, Any]]:
        upload_dir.mkdir(parents=True, exist_ok=True)
        records: list[dict[str, Any]] = []
        for index, (filename, payload) in enumerate(uploads, start=1):
            slot = DEFAULT_UPLOAD_ORDER[index - 1]
            original_name = str(filename or f"frame_{index}.png").strip() or f"frame_{index}.png"
            suffix = Path(original_name).suffix.lower()
            if suffix not in ALLOWED_IMAGE_SUFFIXES:
                raise ValueError(f"Unsupported image type for slot {index}: {original_name}")
            size_bytes = len(payload)
            if size_bytes <= 0:
                raise ValueError(f"Slot {index} is empty.")
            if size_bytes > self.max_upload_bytes:
                raise ValueError(f"Slot {index} exceeds the {self.max_upload_bytes} byte upload limit.")
            width, height = self._validate_image_bytes(payload)
            target_path = upload_dir / f"{index:02d}_{slot['key']}{suffix}"
            target_path.write_bytes(payload)
            records.append(
                {
                    "slot_index": index,
                    "slot_key": slot["key"],
                    "slot_label": slot["label"],
                    "original_name": original_name,
                    "width": width,
                    "height": height,
                    "path": str(target_path),
                }
            )
        return records

    def _validate_image_bytes(self, payload: bytes) -> tuple[int, int]:
        try:
            with Image.open(io.BytesIO(payload)) as image:
                prepared = ImageOps.exif_transpose(image)
                prepared.load()
                width, height = prepared.size
        except Exception as exc:
            raise ValueError("One of the uploaded files is not a valid image.") from exc
        if width < self.min_image_dimension or height < self.min_image_dimension:
            raise ValueError("One of the uploaded images is too small for reliable analysis.")
        if width > self.max_image_dimension or height > self.max_image_dimension:
            raise ValueError("One of the uploaded images is outside the allowed size limits for this mobile API.")
        return width, height

    def _run_job(self, job_id: str) -> None:
        payload = self._read_job(job_id)
        if not payload:
            return
        self._update_job(
            job_id,
            status="running",
            started_at=utc_now_iso(),
            updated_at=utc_now_iso(),
            last_error="",
        )
        upload_paths = [
            str(item.get("path", ""))
            for item in _payload_items(payload.get("uploads", []))
            if str(item.get("path", "")).strip()
        ]
        render_config = _payload_dict(payload.get("settings", {}))
        try:
            result, source_image_state, final_source_path = self.pipeline_adapter.analyze_bundle(upload_paths, render_config)
            normalized_result = self.pipeline_adapter.normalize_result(result)
            artifacts = self._export_job_artifacts(job_id, normalized_result, source_image_state)
            result_payload = self._build_result_payload(
                job_id=job_id,
                result=normalized_result,
                render_config=render_config,
                final_source_path=final_source_path,
                artifacts=artifacts,
            )
            result_path = self.jobs_dir / job_id / "result.json"
            _write_json_atomic(result_path, result_payload)
            self._update_job(
                job_id,
                status="completed",
                completed_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                result_path=str(result_path),
                artifacts=artifacts,
                last_error="",
            )
        except Exception as exc:
            LOGGER.exception("Mobile API job %s failed.", job_id)
            self._update_job(
                job_id,
                status="failed",
                completed_at=utc_now_iso(),
                updated_at=utc_now_iso(),
                last_error=str(exc),
            )

    def _export_job_artifacts(self, job_id: str, result: Mapping[str, Any], source_image_state: Any) -> list[dict[str, Any]]:
        artifact_dir = self.jobs_dir / job_id / "artifacts"
        try:
            exported = self.pipeline_adapter.export_artifacts(result, source_image_state, artifact_dir, job_id)
        except Exception:
            LOGGER.exception("Artifact export failed for mobile API job %s.", job_id)
            exported = []
        return [self._public_artifact_payload(job_id, artifact) for artifact in exported]

    def _build_result_payload(
        self,
        *,
        job_id: str,
        result: Mapping[str, Any],
        render_config: Mapping[str, Any],
        final_source_path: str,
        artifacts: Sequence[Mapping[str, Any]],
    ) -> dict[str, Any]:
        multi_timeframe = _payload_dict(result.get("multi_timeframe", {}))
        entries_payload: list[dict[str, Any]] = []
        artifact_responses = [self._artifact_response_payload(artifact) for artifact in artifacts]
        artifact_index: dict[tuple[str, str], dict[str, Any]] = {}
        global_artifacts: dict[str, dict[str, Any]] = {}
        for artifact in artifact_responses:
            kind = str(artifact.get("kind", "")).strip()
            slot_key = str(artifact.get("slot_key", "")).strip()
            if slot_key:
                artifact_index[(slot_key, kind)] = _payload_dict(artifact)
            else:
                global_artifacts[kind] = _payload_dict(artifact)

        for index, entry in enumerate(_payload_items(multi_timeframe.get("entries", []))[: len(DEFAULT_UPLOAD_ORDER)], start=1):
            slot = DEFAULT_UPLOAD_ORDER[index - 1]
            slot_key = slot["key"]
            entry_payload: dict[str, Any] = {
                "label": str(entry.get("label", slot["label"])),
                "action": str(entry.get("action", "HOLD")).upper(),
                "confidence": float(entry.get("confidence", 0.0) or 0.0),
                "projection_direction": str(entry.get("projection_direction", "HOLD")).upper(),
                "bias_direction": str(entry.get("bias_direction", "HOLD")).upper(),
                "bias_strength": float(entry.get("bias_strength", 0.0) or 0.0),
                "setup": str(entry.get("setup", "")),
                "timeframe": str(entry.get("timeframe", "")),
                "momentum_bias": str(entry.get("momentum_bias", "")),
                "artifacts": {},
            }
            raw_artifact = artifact_index.get((slot_key, "raw"))
            if raw_artifact:
                entry_payload["artifacts"]["raw"] = raw_artifact
            overlay_artifact = artifact_index.get((slot_key, "overlay"))
            if overlay_artifact:
                entry_payload["artifacts"]["overlay"] = overlay_artifact
            entries_payload.append(entry_payload)

        return {
            "job_id": job_id,
            "completed_at": utc_now_iso(),
            "final_source_path": str(final_source_path),
            "action": str(result.get("action", "HOLD")).upper(),
            "headline_action": str(result.get("headline_action", result.get("action", "HOLD"))).upper(),
            "active_trade_state": str(result.get("active_trade_state", "HOLD_TRUE")).upper(),
            "directional_intent": str(result.get("directional_intent", "HOLD")).upper(),
            "confidence": float(result.get("confidence", 0.0) or 0.0),
            "decision_state": str(result.get("decision_state", "")),
            "execution_permission": str(result.get("execution_permission", "")),
            "memory_similarity": float(result.get("memory_similarity", 0.0) or 0.0),
            "projection": _payload_dict(result.get("projection", {})),
            "timestamp": str(result.get("timestamp", "")),
            "render_config": dict(render_config),
            "multi_timeframe": {
                "aligned": bool(multi_timeframe.get("aligned", False)),
                "gate_state": str(multi_timeframe.get("gate_state", "")),
                "summary": str(multi_timeframe.get("summary", "")),
                "entries": entries_payload,
            },
            "artifacts": artifact_responses,
            "overlay_sheet": global_artifacts.get("sheet"),
            "overlay_fusion": global_artifacts.get("fusion"),
            "final_source_artifact": global_artifacts.get("source"),
        }

    def _write_job(self, job_id: str, payload: Mapping[str, Any]) -> None:
        path = self.jobs_dir / job_id / "job.json"
        _write_json_atomic(path, payload)

    def _read_job(self, job_id: str) -> dict[str, Any]:
        path = self.jobs_dir / job_id / "job.json"
        return _payload_dict(_read_json(path, {}))

    def _update_job(self, job_id: str, **updates: Any) -> dict[str, Any]:
        with self._lock:
            payload = self._read_job(job_id)
            if not payload:
                return {}
            payload.update(dict(updates))
            payload["job_id"] = job_id
            payload["updated_at"] = utc_now_iso()
            self._write_job(job_id, payload)
        return payload

    def _public_job_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "job_id": str(payload.get("job_id", "")),
            "status": str(payload.get("status", "")),
            "created_at": str(payload.get("created_at", "")),
            "updated_at": str(payload.get("updated_at", "")),
            "started_at": str(payload.get("started_at", "")),
            "completed_at": str(payload.get("completed_at", "")),
            "last_error": str(payload.get("last_error", "")),
            "settings": _payload_dict(payload.get("settings", {})),
            "upload_order": [dict(item) for item in _payload_items(payload.get("upload_order", []))],
            "uploads": [
                {
                    "slot_index": int(item.get("slot_index", 0) or 0),
                    "slot_key": str(item.get("slot_key", "")),
                    "slot_label": str(item.get("slot_label", "")),
                    "original_name": str(item.get("original_name", "")),
                    "width": int(item.get("width", 0) or 0),
                    "height": int(item.get("height", 0) or 0),
                }
                for item in _payload_items(payload.get("uploads", []))
            ],
            "artifacts": [self._artifact_response_payload(item) for item in _payload_items(payload.get("artifacts", []))],
        }

    def _public_artifact_payload(self, job_id: str, artifact: Mapping[str, Any]) -> dict[str, Any]:
        name = Path(str(artifact.get("name", ""))).name
        return {
            "name": name,
            "kind": str(artifact.get("kind", "")),
            "label": str(artifact.get("label", "")),
            "slot_index": int(artifact.get("slot_index", 0) or 0),
            "slot_key": str(artifact.get("slot_key", "")),
            "slot_label": str(artifact.get("slot_label", "")),
            "url": f"/v1/mobile/jobs/{job_id}/artifacts/{name}",
            "path": str(artifact.get("path", "")),
        }

    def _artifact_response_payload(self, artifact: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "name": Path(str(artifact.get("name", ""))).name,
            "kind": str(artifact.get("kind", "")),
            "label": str(artifact.get("label", "")),
            "slot_index": int(artifact.get("slot_index", 0) or 0),
            "slot_key": str(artifact.get("slot_key", "")),
            "slot_label": str(artifact.get("slot_label", "")),
            "url": str(artifact.get("url", "")),
        }
