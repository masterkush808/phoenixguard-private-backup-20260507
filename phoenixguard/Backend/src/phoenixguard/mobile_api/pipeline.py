from __future__ import annotations

import importlib
import logging
import re
import shutil
from functools import cached_property
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping, Protocol, Sequence, cast

import numpy as np
from PIL import Image


LOGGER = logging.getLogger("phoenixguard.mobile_api.pipeline")

DEFAULT_UPLOAD_ORDER: tuple[dict[str, str], ...] = (
    {"key": "higher_zoomed_out", "label": "Higher TF / Zoomed Out"},
    {"key": "higher_zoomed_in", "label": "Higher TF / Zoomed In"},
    {"key": "lower_zoomed_out", "label": "Lower TF / Zoomed Out"},
    {"key": "lower_zoomed_in", "label": "Lower TF / Zoomed In"},
)
DEFAULT_TIMEFRAME_CHOICES: tuple[str, ...] = ("M1", "M3", "M5", "M15", "M30", "H1", "H4", "D1")
DEFAULT_OVERLAY_MODE = "history-plus-projection"
DEFAULT_COUNCIL_SCOPE = "standard"


class PipelineAdapter(Protocol):
    def describe(self) -> dict[str, Any]:
        ...

    def normalize_render_config(self, settings: Mapping[str, Any] | None) -> dict[str, Any]:
        ...

    def analyze_bundle(
        self,
        upload_paths: Sequence[str],
        render_config: Mapping[str, Any],
    ) -> tuple[dict[str, Any], Any, str]:
        ...

    def normalize_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        ...

    def export_artifacts(
        self,
        result: Mapping[str, Any],
        source_image_state: Any,
        artifact_dir: Path,
        job_id: str,
    ) -> list[dict[str, Any]]:
        ...


def _slugify(value: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "")).strip("._").lower()
    return slug or fallback


def _image_from_state(image_state: Any) -> Image.Image | None:
    if image_state is None:
        return None
    if isinstance(image_state, Image.Image):
        return image_state.convert("RGB")
    try:
        arr = np.asarray(image_state, dtype=np.uint8)
    except Exception:
        return None
    if arr.ndim == 2:
        return Image.fromarray(arr, mode="L").convert("RGB")
    if arr.ndim != 3:
        return None
    channel_count = int(arr.shape[2]) if arr.shape[2:] else 0
    if channel_count == 1:
        return Image.fromarray(arr[:, :, 0], mode="L").convert("RGB")
    if channel_count == 3:
        return Image.fromarray(arr, mode="RGB")
    if channel_count == 4:
        return Image.fromarray(arr, mode="RGBA").convert("RGB")
    return None


def _mapping_dict(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, Any], value))
    return {}


def _mapping_items(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        return []
    items = cast(Sequence[object], value)
    rows: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, Mapping):
            rows.append(dict(cast(Mapping[str, Any], item)))
    return rows


def _require_mapping_dict(value: object, *, context: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(cast(Mapping[str, Any], value))
    raise TypeError(f"{context} must return a mapping, got {type(value).__name__}.")


def _string_choices(value: object, fallback: Sequence[object]) -> list[str]:
    source: Sequence[object] = cast(Sequence[object], value) if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else fallback
    return [str(item) for item in source]


class PhoenixGuardPipelineAdapter:
    @cached_property
    def module(self) -> ModuleType:
        return importlib.import_module("main")

    def describe(self) -> dict[str, Any]:
        module = self.module
        normalize = getattr(module, "_normalize_manual_inference_render_config", None)
        if callable(normalize):
            default_settings = _require_mapping_dict(
                normalize({}),
                context="main._normalize_manual_inference_render_config",
            )
        else:
            default_settings: dict[str, object] = {
                "overlay_mode": getattr(module, "DEFAULT_OVERLAY_MODE", DEFAULT_OVERLAY_MODE),
                "min_conf_global": 0.42,
                "min_conf_latest": 0.50,
                "history_depth": 8,
                "label_density": 10,
                "projection_focus": 0.35,
                "debug_depth": 6,
                "fuse_timeframe_overlays": False,
                "higher_timeframe": "M15",
                "lower_timeframe": "M5",
                "council_scope": getattr(module, "DEFAULT_COUNCIL_SCOPE", DEFAULT_COUNCIL_SCOPE),
            }
        return {
            "required_uploads": len(DEFAULT_UPLOAD_ORDER),
            "upload_order": [dict(slot) for slot in DEFAULT_UPLOAD_ORDER],
            "timeframe_choices": _string_choices(getattr(module, "TIMEFRAME_CHOICES", DEFAULT_TIMEFRAME_CHOICES), DEFAULT_TIMEFRAME_CHOICES),
            "overlay_choices": _string_choices(getattr(module, "VISION_LEVEL_CHOICES", (default_settings["overlay_mode"],)), (default_settings["overlay_mode"],)),
            "council_scope_choices": _string_choices(getattr(module, "COUNCIL_SCOPE_CHOICES", (default_settings["council_scope"],)), (default_settings["council_scope"],)),
            "default_settings": default_settings,
        }

    def normalize_render_config(self, settings: Mapping[str, Any] | None) -> dict[str, Any]:
        payload = dict(settings or {})
        module = self.module
        normalize = getattr(module, "_normalize_manual_inference_render_config", None)
        if callable(normalize):
            return _require_mapping_dict(
                normalize(payload),
                context="main._normalize_manual_inference_render_config",
            )
        build_render_config = getattr(module, "_build_render_config")
        higher_timeframe = payload["higher_timeframe"] if "higher_timeframe" in payload else "M15"
        lower_timeframe = payload["lower_timeframe"] if "lower_timeframe" in payload else "M5"
        return _require_mapping_dict(
            build_render_config(
                overlay_mode=str(payload.get("overlay_mode", getattr(module, "DEFAULT_OVERLAY_MODE", DEFAULT_OVERLAY_MODE))),
                min_conf_global=float(payload.get("min_conf_global", 0.42) or 0.42),
                min_conf_latest=float(payload.get("min_conf_latest", 0.50) or 0.50),
                history_depth=float(payload.get("history_depth", 8) or 8),
                label_density=float(payload.get("label_density", 10) or 10),
                projection_focus=float(payload.get("projection_focus", 0.35) or 0.35),
                debug_depth=float(payload.get("debug_depth", 6) or 6),
                fuse_timeframe_overlays=bool(payload.get("fuse_timeframe_overlays", False)),
                vision_extras=payload.get("vision_extras"),
                council_scope=payload.get("council_scope", getattr(module, "DEFAULT_COUNCIL_SCOPE", DEFAULT_COUNCIL_SCOPE)),
                higher_timeframe=str(higher_timeframe or ""),
                lower_timeframe=str(lower_timeframe or ""),
            ),
            context="main._build_render_config",
        )

    def analyze_bundle(
        self,
        upload_paths: Sequence[str],
        render_config: Mapping[str, Any],
    ) -> tuple[dict[str, Any], Any, str]:
        analyze = getattr(self.module, "_analyze_manual_multi_timeframe_once")
        result, source_image_state, file_path = analyze(list(upload_paths), dict(render_config))
        return (
            _require_mapping_dict(
                result,
                context="main._analyze_manual_multi_timeframe_once",
            ),
            source_image_state,
            str(file_path),
        )

    def normalize_result(self, result: Mapping[str, Any]) -> dict[str, Any]:
        ensure = getattr(self.module, "_ensure_active_trade_overlay", None)
        if callable(ensure):
            return _require_mapping_dict(
                ensure(dict(result)),
                context="main._ensure_active_trade_overlay",
            )
        return dict(result)

    def export_artifacts(
        self,
        result: Mapping[str, Any],
        source_image_state: Any,
        artifact_dir: Path,
        job_id: str,
    ) -> list[dict[str, Any]]:
        module = self.module
        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifacts: list[dict[str, Any]] = []
        multi_timeframe = _mapping_dict(result.get("multi_timeframe", {}))
        entries = _mapping_items(multi_timeframe.get("entries", []))
        for index, entry in enumerate(entries[: len(DEFAULT_UPLOAD_ORDER)], start=1):
            slot = DEFAULT_UPLOAD_ORDER[index - 1]
            label = str(entry.get("label", slot["label"])).strip() or slot["label"]
            slot_key = _slugify(slot["key"], f"slot_{index}")
            for kind in ("raw", "overlay"):
                source_path = str(entry.get(f"{kind}_asset_path", "")).strip()
                if not source_path:
                    continue
                copied = self._copy_image_artifact(Path(source_path), artifact_dir, f"{index:02d}_{slot_key}_{kind}")
                if copied is None:
                    continue
                artifacts.append(
                    {
                        "name": copied.name,
                        "kind": kind,
                        "label": f"{label} {kind.title()}",
                        "slot_index": index,
                        "slot_key": slot["key"],
                        "slot_label": label,
                        "path": str(copied),
                    }
                )

        sheet_builder = getattr(module, "_build_multi_timeframe_overlay_sheet", None)
        if callable(sheet_builder):
            try:
                sheet_image = sheet_builder(result)
            except Exception:
                LOGGER.exception("Failed to build multi-timeframe overlay sheet for job %s.", job_id)
                sheet_image = None
            if isinstance(sheet_image, Image.Image):
                sheet_path = artifact_dir / "multi_timeframe_sheet.png"
                sheet_image.save(sheet_path, format="PNG")
                artifacts.append(
                    {
                        "name": sheet_path.name,
                        "kind": "sheet",
                        "label": "Multi-Timeframe Sheet",
                        "path": str(sheet_path),
                    }
                )

        fusion_builder = getattr(module, "_build_multi_timeframe_overlay_fusion", None)
        if callable(fusion_builder):
            try:
                fusion_image = fusion_builder(result)
            except Exception:
                LOGGER.exception("Failed to build multi-timeframe overlay fusion for job %s.", job_id)
                fusion_image = None
            if isinstance(fusion_image, Image.Image):
                fusion_path = artifact_dir / "multi_timeframe_fusion.png"
                fusion_image.save(fusion_path, format="PNG")
                artifacts.append(
                    {
                        "name": fusion_path.name,
                        "kind": "fusion",
                        "label": "Timeframe Fusion",
                        "path": str(fusion_path),
                    }
                )

        source_image = _image_from_state(source_image_state)
        if source_image is not None:
            source_path = artifact_dir / "final_source.png"
            source_image.save(source_path, format="PNG")
            artifacts.append(
                {
                    "name": source_path.name,
                    "kind": "source",
                    "label": "Final Source Chart",
                    "path": str(source_path),
                }
            )
        return artifacts

    def _copy_image_artifact(self, source_path: Path, artifact_dir: Path, base_name: str) -> Path | None:
        if not source_path.exists() or not source_path.is_file():
            return None
        suffix = source_path.suffix if source_path.suffix else ".png"
        target_path = artifact_dir / f"{base_name}{suffix}"
        try:
            shutil.copyfile(source_path, target_path)
        except Exception:
            LOGGER.exception("Failed to copy artifact %s.", source_path)
            return None
        return target_path
