from __future__ import annotations

from dataclasses import dataclass
import importlib
import os
from threading import Lock
from typing import Any, cast

import numpy as np
from PIL import Image
import torch

from phoenixguard.core.config import MODELS, RUNTIME


def _clip01(value: Any, default: float = 0.0) -> float:
    try:
        return float(np.clip(float(value), 0.0, 1.0))
    except Exception:
        return float(default)


def _to_device_inputs(payload: dict[str, Any], device: torch.device) -> dict[str, Any]:
    moved: dict[str, Any] = {}
    for key, value in payload.items():
        if hasattr(value, "to"):
            try:
                moved[key] = value.to(device)
                continue
            except Exception:
                pass
        moved[key] = value
    return moved


def _pillow_bilinear() -> Any:
    resampling = getattr(Image, "Resampling", None)
    if resampling is not None:
        return getattr(resampling, "BILINEAR", getattr(Image, "BILINEAR", 2))
    return getattr(Image, "BILINEAR", 2)


def _remote_bootstrap_enabled(env_name: str) -> bool:
    return str(os.getenv(env_name, "") or "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(slots=True)
class GroundedBackendResult:
    caption: str
    detections: list[dict[str, Any]]
    masks: list[dict[str, Any]]
    used_backends: list[str]
    errors: dict[str, str]

    @property
    def confidence(self) -> float:
        scores = [float(item.get("score", 0.0) or 0.0) for item in self.detections]
        if not scores:
            return 0.0
        return float(np.clip(np.mean(np.asarray(scores, dtype=np.float32)), 0.0, 1.0))


class OptionalGroundedParser:
    def __init__(self, logger: Any) -> None:
        self.logger = logger
        self.device = torch.device(RUNTIME.device_preference)
        self._lock = Lock()
        self._loaded = False
        self._transformers: Any | None = None
        self._florence_processor: Any | None = None
        self._florence_model: Any | None = None
        self._grounding_processor: Any | None = None
        self._grounding_model: Any | None = None
        self._sam_processor: Any | None = None
        self._sam_model: Any | None = None
        self._errors: dict[str, str] = {}

    def _local_files_only(self) -> bool:
        if bool(RUNTIME.allow_offline_only):
            return True
        return not _remote_bootstrap_enabled("PHOENIXGUARD_GROUNDED_ALLOW_REMOTE_BOOTSTRAP")

    def _load_transformers(self) -> Any | None:
        if self._transformers is not None:
            return self._transformers
        try:
            self._transformers = importlib.import_module("transformers")
            return self._transformers
        except Exception as exc:
            self._errors["transformers"] = str(exc)
            return None

    def _load_backends(self) -> None:
        if self._loaded:
            return
        with self._lock:
            if self._loaded:
                return
            transformers = self._load_transformers()
            if transformers is None:
                self._loaded = True
                return

            try:
                auto_processor = getattr(transformers, "AutoProcessor")
                auto_causal = getattr(transformers, "AutoModelForCausalLM")
                local_files_only = self._local_files_only()
                self._florence_processor = auto_processor.from_pretrained(
                    MODELS.florence2_model,
                    local_files_only=local_files_only,
                    trust_remote_code=True,
                )
                self._florence_model = auto_causal.from_pretrained(
                    MODELS.florence2_model,
                    local_files_only=local_files_only,
                    trust_remote_code=True,
                ).to(self.device)
                self._florence_model.eval()
            except Exception as exc:
                self._errors["florence2"] = str(exc)

            try:
                auto_processor = getattr(transformers, "AutoProcessor")
                auto_grounding = getattr(transformers, "AutoModelForZeroShotObjectDetection")
                local_files_only = self._local_files_only()
                self._grounding_processor = auto_processor.from_pretrained(
                    MODELS.grounding_dino_model,
                    local_files_only=local_files_only,
                )
                self._grounding_model = auto_grounding.from_pretrained(
                    MODELS.grounding_dino_model,
                    local_files_only=local_files_only,
                ).to(self.device)
                self._grounding_model.eval()
            except Exception as exc:
                self._errors["grounding_dino"] = str(exc)

            try:
                sam_processor = getattr(transformers, "SamProcessor")
                sam_model_cls = getattr(transformers, "SamModel")
                local_files_only = self._local_files_only()
                self._sam_processor = sam_processor.from_pretrained(
                    MODELS.sam2_model,
                    local_files_only=local_files_only,
                )
                self._sam_model = sam_model_cls.from_pretrained(
                    MODELS.sam2_model,
                    local_files_only=local_files_only,
                ).to(self.device)
                self._sam_model.eval()
            except Exception as exc:
                self._errors["sam2"] = str(exc)

            self._loaded = True

    def _florence_caption(self, image: Image.Image) -> str:
        if self._florence_model is None or self._florence_processor is None:
            return ""
        try:
            task = "<DENSE_REGION_CAPTION>"
            encoded = cast(
                dict[str, Any],
                self._florence_processor(text=task, images=image, return_tensors="pt"),
            )
            encoded = _to_device_inputs(encoded, self.device)
            with torch.inference_mode():
                generated = self._florence_model.generate(
                    input_ids=encoded.get("input_ids"),
                    pixel_values=encoded.get("pixel_values"),
                    max_new_tokens=96,
                    num_beams=2,
                )
            decoded = self._florence_processor.batch_decode(generated, skip_special_tokens=False)[0]
            post_process = getattr(self._florence_processor, "post_process_generation", None)
            if callable(post_process):
                parsed = post_process(decoded, task=task, image_size=(image.width, image.height))
                if isinstance(parsed, dict):
                    for value in parsed.values():
                        if isinstance(value, str) and value.strip():
                            return value.strip()
                if isinstance(parsed, str) and parsed.strip():
                    return parsed.strip()
            return str(decoded).strip()
        except Exception as exc:
            self._errors["florence2_infer"] = str(exc)
            return ""

    def _grounding_dino_detect(self, image: Image.Image) -> list[dict[str, Any]]:
        if self._grounding_model is None or self._grounding_processor is None:
            return []
        try:
            prompt = (
                "candlestick . wick . support zone . resistance zone . breakout box . "
                "pullback box . consolidation . broker ui . cursor . watermark . artifact ."
            )
            encoded = cast(
                dict[str, Any],
                self._grounding_processor(images=image, text=prompt, return_tensors="pt"),
            )
            encoded = _to_device_inputs(encoded, self.device)
            with torch.inference_mode():
                outputs = self._grounding_model(**encoded)
            post_process = getattr(self._grounding_processor, "post_process_grounded_object_detection", None)
            if not callable(post_process):
                return []
            processed = post_process(
                outputs,
                encoded.get("input_ids"),
                box_threshold=0.18,
                text_threshold=0.18,
                target_sizes=[(image.height, image.width)],
            )
            if not processed:
                return []
            first = cast(dict[str, Any], processed[0])
            boxes = first.get("boxes", [])
            scores = first.get("scores", [])
            labels = first.get("labels", [])
            detections: list[dict[str, Any]] = []
            for box, score, label in zip(boxes, scores, labels):
                bbox = (
                    [float(x) for x in box.detach().cpu().view(-1).tolist()]
                    if hasattr(box, "detach")
                    else [float(x) for x in box]
                )
                label_text = str(label)
                detections.append(
                    {
                        "label": label_text.strip().lower().replace(" ", "_"),
                        "score": _clip01(score, 0.0),
                        "bbox": bbox[:4] if len(bbox) >= 4 else [0.0, 0.0, 0.0, 0.0],
                        "source": "grounding_dino",
                    }
                )
            return detections
        except Exception as exc:
            self._errors["grounding_dino_infer"] = str(exc)
            return []

    def _sam_segment(
        self,
        image: Image.Image,
        detections: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self._sam_model is None or self._sam_processor is None or not detections:
            return []
        boxes = [
            [cast(list[float], detection.get("bbox", [0.0, 0.0, 0.0, 0.0]))]
            for detection in detections[:8]
        ]
        try:
            encoded = cast(
                dict[str, Any],
                self._sam_processor(image, input_boxes=boxes, return_tensors="pt"),
            )
            encoded = _to_device_inputs(encoded, self.device)
            with torch.inference_mode():
                outputs = self._sam_model(**encoded)
            post_process = getattr(self._sam_processor, "post_process_masks", None)
            if not callable(post_process):
                return []
            masks = post_process(
                outputs.pred_masks.detach().cpu(),
                encoded.get("original_sizes"),
                encoded.get("reshaped_input_sizes"),
            )
            summaries: list[dict[str, Any]] = []
            for detection, mask_group in zip(detections, masks):
                if isinstance(mask_group, list) and mask_group:
                    first_mask = mask_group[0]
                else:
                    first_mask = mask_group
                mask_array = np.asarray(first_mask, dtype=np.float32)
                if mask_array.ndim > 2:
                    mask_array = np.squeeze(mask_array)
                mask_array = np.asarray(mask_array, dtype=np.float32)
                binary_mask = mask_array >= 0.5
                if binary_mask.ndim != 2 or not np.any(binary_mask):
                    continue
                ys, xs = np.where(binary_mask)
                y1 = int(np.min(ys))
                y2 = int(np.max(ys)) + 1
                x1 = int(np.min(xs))
                x2 = int(np.max(xs)) + 1
                mask_crop = binary_mask[y1:y2, x1:x2]
                if mask_crop.size == 0:
                    continue
                mask_image = Image.fromarray(np.uint8(mask_crop) * 255, mode="L")
                mask_grid_size = 18
                mask_grid = np.asarray(
                    mask_image.resize((mask_grid_size, mask_grid_size), resample=_pillow_bilinear()),
                    dtype=np.uint8,
                )
                coverage_bbox_ratio = float(np.clip(mask_crop.mean(), 0.0, 1.0))
                summaries.append(
                    {
                        "label": str(detection.get("label", "")),
                        "source": "sam2",
                        "score": _clip01(detection.get("score", 0.0)),
                        "bbox": [float(x1), float(y1), float(x2), float(y2)],
                        "mask_area_ratio": float(np.clip(mask_array.mean(), 0.0, 1.0)),
                        "coverage_bbox_ratio": coverage_bbox_ratio,
                        "grid_size": mask_grid_size,
                        "mask_grid": mask_grid.tolist(),
                    }
                )
            return summaries
        except Exception as exc:
            self._errors["sam2_infer"] = str(exc)
            return []

    def parse(self, image: Image.Image) -> GroundedBackendResult:
        self._load_backends()
        errors_before = dict(self._errors)
        used_backends: list[str] = []
        caption = self._florence_caption(image)
        if caption:
            used_backends.append("florence2")
        detections = self._grounding_dino_detect(image)
        if detections:
            used_backends.append("grounding_dino")
        masks = self._sam_segment(image, detections)
        if masks:
            used_backends.append("sam2")
        active_errors = {
            key: value
            for key, value in self._errors.items()
            if errors_before.get(key) != value or key not in errors_before
        }
        return GroundedBackendResult(
            caption=caption,
            detections=detections,
            masks=masks,
            used_backends=used_backends,
            errors=active_errors,
        )
