"""
PhoenixGuard Integrated Vision Module v2.0
===========================================
Orchestrates the complete vision pipeline:
  1. Multi-model ensemble (ViT + YOLO + SAM)
  2. Optical flow motion tracking
  3. Chart segmentation and extraction
  4. Feature fusion and explainability

This is the new CV_ENGINE interface replacing the legacy cv_module.py
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from phoenixguard.vision.multi_model_ensemble import (
    MultiModelEnsemble,
    ModelRegistry,
    VisionEnsembleOutput,
    Detection,
)
from phoenixguard.vision.motion_tracker import OpticalFlowTracker, OpticalFlowFrame
from phoenixguard.vision.chart_segmentation import ChartSegmentationEngine, ChartSegmentation


logger = logging.getLogger(__name__)


@dataclass
class EnhancedVisionOutput:
    """Complete output from upgraded vision module."""
    # Multi-model detection results
    detections: list[Detection]
    detection_confidence: float
    
    # Motion analysis
    motion_frame: Optional[OpticalFlowFrame]
    motion_energy: float
    consolidation_score: float
    breakout_score: float
    
    # Chart segmentation
    chart_segmentation: Optional[ChartSegmentation]
    chart_mask: Optional[NDArray[np.uint8]]
    chart_region_cropped: Optional[Image.Image]
    
    # Semantic features
    semantic_features: Optional[NDArray[np.float32]]
    attention_heatmap: Optional[NDArray[np.float32]]
    
    # Metadata
    inference_time_ms: float
    model_status: dict[str, bool]
    quality_score: float  # 0-1, composite quality metric
    
    # Explainability
    attribution_scores: dict[str, float]  # Per-model contribution
    feature_importance: dict[str, float]  # Top contributing features


class EnhancedVisionEngine:
    """
    Industry-grade vision engine combining multiple modalities.

    Usage:
        engine = EnhancedVisionEngine()
        output = engine.process_frame(image)
    """

    def __init__(
        self,
        enable_vit: bool = True,
        enable_yolo: bool = True,
        enable_sam: bool = True,
        enable_optical_flow: bool = True,
        motion_window: int = 5,
    ):
        """
        Args:
            enable_vit: Use Vision Transformer for semantic features
            enable_yolo: Use YOLOv8 for precise detection
            enable_sam: Use Segment Anything Model for chart boundaries
            enable_optical_flow: Track motion across frames
            motion_window: Number of frames for motion aggregation
        """
        self.enable_vit = enable_vit
        self.enable_yolo = enable_yolo
        self.enable_sam = enable_sam
        self.enable_optical_flow = enable_optical_flow

        # Initialize model registry
        self.model_registry = ModelRegistry()
        self._load_models()

        # Multi-model ensemble
        self.ensemble = MultiModelEnsemble(
            self.model_registry,
            yolo_weight=0.5,
            vit_weight=0.3,
            sam_weight=0.2,
        )

        # Optical flow tracker
        self.motion_tracker = OpticalFlowTracker(
            accumulation_window=motion_window,
            motion_threshold=0.5,
            consolidation_threshold=0.2,
        ) if enable_optical_flow else None

        # Chart segmentation
        self.segmentation_engine = ChartSegmentationEngine(
            sam_predictor=self.model_registry.models.get("sam"),
            min_chart_ratio=0.2,
            max_chart_ratio=0.95,
        ) if enable_sam else None

        self.frame_counter = 0

    def _load_models(self):
        """Load all enabled models."""
        if self.enable_yolo:
            self.model_registry.register_yolo_model("yolov8m.pt")

        if self.enable_vit:
            self.model_registry.register_vit_model("vit_base_patch16_384")

        if self.enable_sam:
            try:
                self.model_registry.register_sam_model("vit_b")
            except Exception as e:
                logger.warning(f"SAM registration warning: {e}")

    def process_frame(
        self,
        image: Image.Image | NDArray[np.uint8],
        timestamp_ms: float = 0.0,
    ) -> EnhancedVisionOutput:
        """
        Process single frame through complete vision pipeline.

        Args:
            image: Input image (PIL Image or numpy array)
            timestamp_ms: Frame timestamp

        Returns:
            EnhancedVisionOutput with all analysis results
        """
        start_time = time.time()

        # Convert input
        if isinstance(image, Image.Image):
            img_array = np.array(image)
        else:
            img_array = image

        # 1. Multi-model ensemble inference
        ensemble_output = self.ensemble.infer(image)

        # 2. Optical flow motion analysis
        motion_frame = None
        if self.motion_tracker is not None:
            motion_frame = self.motion_tracker.process_frame(image, timestamp_ms)

        # 3. Chart segmentation
        chart_segmentation = None
        chart_region_cropped = None
        if self.segmentation_engine is not None:
            chart_segmentation = self.segmentation_engine.segment_chart(image)
            if chart_segmentation.is_valid:
                chart_region_cropped, _ = self.segmentation_engine.extract_chart_region(
                    image, chart_segmentation
                )

        # 4. Compute quality score
        quality_score = self._compute_quality_score(
            ensemble_output,
            motion_frame,
            chart_segmentation,
        )

        # 5. Compute attribution
        attribution_scores = self._compute_attribution(ensemble_output)

        # 6. Feature importance
        feature_importance = self._compute_feature_importance(ensemble_output, motion_frame)

        inference_time_ms = (time.time() - start_time) * 1000

        output = EnhancedVisionOutput(
            detections=ensemble_output.detections,
            detection_confidence=np.mean([d.confidence for d in ensemble_output.detections]) if ensemble_output.detections else 0.0,
            motion_frame=motion_frame,
            motion_energy=motion_frame.motion_energy if motion_frame else 0.0,
            consolidation_score=motion_frame.consolidation_score if motion_frame else 1.0,
            breakout_score=motion_frame.breakout_score if motion_frame else 0.0,
            chart_segmentation=chart_segmentation,
            chart_mask=chart_segmentation.mask if chart_segmentation else None,
            chart_region_cropped=chart_region_cropped,
            semantic_features=ensemble_output.semantic_features,
            attention_heatmap=ensemble_output.attention_map,
            inference_time_ms=inference_time_ms,
            model_status=ensemble_output.model_active_status,
            quality_score=quality_score,
            attribution_scores=attribution_scores,
            feature_importance=feature_importance,
        )

        self.frame_counter += 1
        logger.debug(f"Frame {self.frame_counter}: {inference_time_ms:.1f}ms, quality={quality_score:.2f}")

        return output

    def _compute_quality_score(
        self,
        ensemble_output: VisionEnsembleOutput,
        motion_frame: Optional[OpticalFlowFrame],
        chart_segmentation: Optional[ChartSegmentation],
    ) -> float:
        """
        Compute composite quality score (0-1).

        Factors:
          - Detection confidence (40%)
          - Chart segmentation validity (30%)
          - Motion consistency (20%)
          - Overall sensor health (10%)
        """
        score = 0.0

        # Detection confidence
        if ensemble_output.detections:
            avg_confidence = np.mean([d.confidence for d in ensemble_output.detections])
            score += avg_confidence * 0.4
        else:
            score += 0.2 * 0.4  # Minimum score for no detections

        # Chart segmentation
        if chart_segmentation is not None:
            if chart_segmentation.is_valid:
                score += chart_segmentation.confidence * 0.3
            else:
                score += 0.2 * 0.3  # Penalty for invalid segmentation

        # Motion consistency
        if motion_frame is not None:
            consistency = motion_frame.consistency  # 0-1
            consistency = max(0, consistency)  # Clamp negative values
            score += consistency * 0.2

        # Model health
        active_models = sum(ensemble_output.model_active_status.values())
        total_models = len(ensemble_output.model_active_status)
        model_health = active_models / max(total_models, 1)
        score += model_health * 0.1

        return float(np.clip(score, 0, 1))

    def _compute_attribution(self, ensemble_output: VisionEnsembleOutput) -> dict[str, float]:
        """Compute per-model contribution to detection."""
        scores = ensemble_output.fusion_scores.copy()

        # Normalize
        total = sum(scores.values()) or 1.0
        for key in scores:
            scores[key] = scores[key] / total

        return scores

    def _compute_feature_importance(
        self,
        ensemble_output: VisionEnsembleOutput,
        motion_frame: Optional[OpticalFlowFrame],
    ) -> dict[str, float]:
        """Compute importance of top contributing features."""
        importance = {}

        # YOLO detection confidence
        if ensemble_output.detections:
            importance["detection_confidence"] = np.mean([d.confidence for d in ensemble_output.detections])
        else:
            importance["detection_confidence"] = 0.0

        # ViT semantic strength
        if ensemble_output.semantic_features is not None:
            importance["semantic_features"] = float(np.linalg.norm(ensemble_output.semantic_features))
        else:
            importance["semantic_features"] = 0.0

        # Motion energy
        if motion_frame is not None:
            importance["motion_energy"] = min(1.0, motion_frame.motion_energy / 100.0)
            importance["consolidation"] = motion_frame.consolidation_score
            importance["breakout"] = motion_frame.breakout_score
        else:
            importance["motion_energy"] = 0.0
            importance["consolidation"] = 0.0
            importance["breakout"] = 0.0

        # Normalize to top-10
        sorted_features = sorted(importance.items(), key=lambda x: x[1], reverse=True)[:10]
        return dict(sorted_features)

    def get_diagnostics(self) -> dict[str, Any]:
        """Return detailed diagnostics for debugging."""
        return {
            "frame_count": self.frame_counter,
            "ensemble_stats": self.ensemble.get_stats(),
            "motion_tracker_active": self.motion_tracker is not None,
            "segmentation_engine_active": self.segmentation_engine is not None,
            "model_registry_status": self.model_registry.model_load_status,
            "segmentation_history_length": len(self.segmentation_engine.get_history()) if self.segmentation_engine else 0,
        }

    def reset(self):
        """Reset all state (for new video/stream)."""
        self.frame_counter = 0
        if self.motion_tracker:
            self.motion_tracker.reset()
        if self.segmentation_engine:
            self.segmentation_engine.reset_history()
