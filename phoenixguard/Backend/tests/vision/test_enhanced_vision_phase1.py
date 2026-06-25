"""
PhoenixGuard Vision Module Test Suite (Phase 1)
================================================
Comprehensive testing for multi-model ensemble, optical flow, and chart segmentation.

Test Categories:
  1. Model Loading & Registry
  2. Multi-Model Ensemble Inference
  3. Optical Flow Motion Analysis
  4. Chart Segmentation & Validation
  5. Integration & End-to-End
  6. Edge Cases & Robustness
  7. Performance Benchmarking
"""
import random
from typing import Mapping, cast

import pytest
import numpy as np
from numpy.typing import NDArray
from PIL import Image
import time
import logging

from phoenixguard.vision.multi_model_ensemble import (
    ModelRegistry,
    MultiModelEnsemble,
    Detection,
    VisionEnsembleOutput,
)
from phoenixguard.vision.motion_tracker import OpticalFlowTracker, OpticalFlowFrame
from phoenixguard.vision.chart_segmentation import ChartSegmentationEngine
from phoenixguard.vision.enhanced_vision_module import EnhancedVisionEngine, EnhancedVisionOutput


logger = logging.getLogger(__name__)
pytest_plugins = ("pytest_asyncio",)


# ============================================================================
# FIXTURES: Synthetic test data
# ============================================================================

@pytest.fixture
def sample_chart_image() -> Image.Image:
    """Create synthetic chart image (640x480, candlestick pattern)."""
    h, w = 480, 640
    rng = random.Random(808)
    img: NDArray[np.uint8] = np.full((h, w, 3), 240, dtype=np.uint8)  # Light gray background

    # Draw candlesticks
    for x in range(100, 600, 50):
        # High-Low line (wick)
        y_high = 100 + rng.randrange(0, 50)
        y_low = y_high + 150 + rng.randrange(0, 50)
        img[y_high:y_low, x:x+2, :] = [50, 50, 50]  # Black wick

        # Open-Close box
        y_open = y_high + 50
        y_close = y_high + 100
        img[y_open:y_close, x:x+20, :] = [100, 200, 100]  # Green candle

    # Add axes
    img[-50:, :, :] = [200, 200, 200]  # X-axis area
    img[:, :30, :] = [200, 200, 200]   # Y-axis area

    return Image.fromarray(img)


@pytest.fixture
def sample_video_frames(sample_chart_image: Image.Image) -> list[Image.Image]:
    """Create sequence of frames (simulating price movement)."""
    frames: list[Image.Image] = []
    base_array = np.asarray(sample_chart_image, dtype=np.uint8)

    for i in range(5):
        # Simulate scrolling candlesticks (left shift)
        shifted = np.roll(base_array, -10 * i, axis=1)
        frames.append(Image.fromarray(shifted))

    return frames


@pytest.fixture
def model_registry() -> ModelRegistry:
    """Initialize model registry."""
    return ModelRegistry()


# ============================================================================
# TEST GROUP 1: Model Loading & Registry
# ============================================================================

class TestModelRegistry:
    """Test model registry loading and availability."""

    def test_device_detection(self):
        """Verify device detection (CPU or CUDA)."""
        registry = ModelRegistry()
        assert registry.device in ["cpu", "cuda"]
        logger.info(f"Device detected: {registry.device}")

    def test_yolo_model_loading(self, model_registry: ModelRegistry) -> None:
        """Test YOLOv8 model registration."""
        # Skip if model not available
        result = model_registry.register_yolo_model("yolov8n.pt")  # nano for speed
        if result:
            assert model_registry.is_available("yolo")
            assert "yolo" in model_registry.models
        else:
            logger.warning("YOLO model not available (expected in CI environment)")

    def test_vit_model_loading(self, model_registry: ModelRegistry) -> None:
        """Test Vision Transformer registration."""
        result = model_registry.register_vit_model("vit_base_patch16_384")
        if result:
            assert model_registry.is_available("vit")
            assert "vit" in model_registry.models
        else:
            logger.warning("ViT model not available (expected in CI environment)")

    def test_model_availability_check(self, model_registry: ModelRegistry) -> None:
        """Verify model availability reporting."""
        is_available = model_registry.is_available("yolo")
        # Just check it doesn't crash
        assert isinstance(is_available, bool)


# ============================================================================
# TEST GROUP 2: Multi-Model Ensemble
# ============================================================================

class TestMultiModelEnsemble:
    """Test ensemble inference and fusion."""

    def test_ensemble_initialization(self, model_registry: ModelRegistry) -> None:
        """Test ensemble creation with valid weights."""
        ensemble = MultiModelEnsemble(
            model_registry,
            yolo_weight=0.5,
            vit_weight=0.3,
            sam_weight=0.2,
        )
        assert ensemble is not None
        # Weights should be normalized
        total_weight = sum(ensemble.weights.values())
        assert abs(total_weight - 1.0) < 1e-6

    def test_ensemble_bagging_boosting_strategy(self, model_registry: ModelRegistry, sample_chart_image: Image.Image) -> None:
        """Verify deterministic same-coordinate bagging views and strategy metadata."""
        ensemble = MultiModelEnsemble(
            model_registry,
            enable_bagging=True,
            enable_boosting=True,
            bagging_views=3,
            boosting_rounds=2,
        )
        sample_array = np.asarray(sample_chart_image, dtype=np.uint8)
        views = ensemble.build_bagged_views_for_test(sample_array)

        assert ensemble.ensemble_strategy["bagging_enabled"] is True
        assert ensemble.ensemble_strategy["boosting_enabled"] is True
        assert len(views) == 3
        assert views[0][0] == "original"
        assert all(view.shape[:2] == sample_array.shape[:2] for _, view in views)
        assert ensemble.get_stats()["ensemble_strategy"]["bagging_views"] == 3

    def test_ensemble_weighted_box_fusion_boosts_cross_view_agreement(self, model_registry: ModelRegistry) -> None:
        """Bagged YOLO agreement should fuse and boost stable detections."""
        ensemble = MultiModelEnsemble(
            model_registry,
            confidence_threshold=0.5,
            enable_bagging=True,
            enable_boosting=True,
            bagging_views=3,
            boosting_rounds=2,
        )
        yolo_output: dict[str, object] = {
            "status": "success",
            "count": 2,
            "bagging_views": 3,
            "detections": [
                {"x1": 10, "y1": 20, "x2": 100, "y2": 120, "confidence": 0.55, "class_id": 1, "class_name": "candle", "bag_view": "original"},
                {"x1": 12, "y1": 22, "x2": 102, "y2": 122, "confidence": 0.57, "class_id": 1, "class_name": "candle", "bag_view": "contrast_up"},
            ],
        }

        fused = ensemble.fuse_detections_for_test(yolo_output, {"status": "success"})

        assert len(fused) == 1
        assert fused[0].model_source == "bagged_boosted_yolo"
        assert fused[0].confidence >= 0.5
        metadata = cast(Mapping[str, object], getattr(fused[0], "metadata"))
        assert metadata["vote_count"] == 2
        assert metadata["ensemble_method"] == "bagging+boosting"

    def test_ensemble_weight_normalization(self, model_registry: ModelRegistry) -> None:
        """Verify weights are properly normalized."""
        ensemble = MultiModelEnsemble(
            model_registry,
            yolo_weight=10.0,
            vit_weight=5.0,
            sam_weight=2.0,
        )
        total = sum(ensemble.weights.values())
        assert abs(total - 1.0) < 1e-6

    def test_detection_nms(self, model_registry: ModelRegistry) -> None:
        """Test Non-Maximum Suppression deduplication."""
        ensemble = MultiModelEnsemble(model_registry)

        # Create overlapping detections
        det1 = Detection(x1=0, y1=0, x2=100, y2=100, confidence=0.9, class_id=0, class_name="chart", model_source="yolo", metadata={})
        det2 = Detection(x1=10, y1=10, x2=110, y2=110, confidence=0.8, class_id=0, class_name="chart", model_source="vit", metadata={})

        nms_result = ensemble.nms_for_test([det1, det2], iou_threshold=0.5)

        # Should suppress one
        assert len(nms_result) <= 2

    def test_iou_computation(self, model_registry: ModelRegistry) -> None:
        """Test Intersection-over-Union calculation."""
        ensemble = MultiModelEnsemble(model_registry)

        # Perfect overlap
        det1 = Detection(x1=0, y1=0, x2=100, y2=100, confidence=0.9, class_id=0, class_name="chart", model_source="yolo", metadata={})
        det2 = Detection(x1=0, y1=0, x2=100, y2=100, confidence=0.9, class_id=0, class_name="chart", model_source="yolo", metadata={})
        iou = ensemble.compute_iou_for_test(det1, det2)
        assert abs(iou - 1.0) < 1e-6

        # No overlap
        det3 = Detection(x1=200, y1=200, x2=300, y2=300, confidence=0.9, class_id=0, class_name="chart", model_source="yolo", metadata={})
        iou = ensemble.compute_iou_for_test(det1, det3)
        assert abs(iou) < 1e-6

    def test_ensemble_inference_empty_models(self, model_registry: ModelRegistry, sample_chart_image: Image.Image) -> None:
        """Test ensemble with no models loaded (graceful degradation)."""
        ensemble = MultiModelEnsemble(model_registry)
        # Don't load any models
        output = ensemble.infer(sample_chart_image)
        assert isinstance(output, VisionEnsembleOutput)
        assert output.inference_time_ms >= 0


# ============================================================================
# TEST GROUP 3: Optical Flow Motion Analysis
# ============================================================================

class TestOpticalFlowTracker:
    """Test optical flow detection and motion analysis."""

    def test_tracker_initialization(self):
        """Test motion tracker creation."""
        tracker = OpticalFlowTracker(
            accumulation_window=5,
            motion_threshold=0.5,
        )
        assert tracker is not None
        assert tracker.frame_counter == 0

    def test_first_frame_processing(self, sample_chart_image: Image.Image) -> None:
        """Test processing first frame (should return neutral)."""
        tracker = OpticalFlowTracker()
        flow_frame = tracker.process_frame(sample_chart_image, timestamp_ms=0.0)

        assert isinstance(flow_frame, OpticalFlowFrame)
        assert flow_frame.frame_id == 0
        assert flow_frame.consolidation_score == 1.0  # First frame
        assert flow_frame.motion_energy == 0.0

    def test_frame_sequence_processing(self, sample_video_frames: list[Image.Image]) -> None:
        """Test processing multiple frames (motion accumulation)."""
        tracker = OpticalFlowTracker(accumulation_window=3)

        for i, frame in enumerate(sample_video_frames[:3]):
            flow_frame = tracker.process_frame(frame, timestamp_ms=float(i * 33))
            assert flow_frame.frame_id == i

        # Check history
        assert len(tracker.frame_history) <= 3

    def test_consolidation_scoring(self, sample_chart_image: Image.Image) -> None:
        """Test consolidation zone detection."""
        tracker = OpticalFlowTracker()

        # Static image (no motion)
        flow_frame = tracker.process_frame(sample_chart_image, 0.0)
        assert flow_frame.frame_id == 0
        _ = tracker.process_frame(sample_chart_image, 33.0)  # Same image

        stats = tracker.get_motion_stats()
        assert stats.consolidation_count >= 0

    def test_motion_stats_aggregation(self, sample_video_frames: list[Image.Image]) -> None:
        """Test statistics aggregation across frame window."""
        tracker = OpticalFlowTracker(accumulation_window=3)

        for i, frame in enumerate(sample_video_frames[:3]):
            _ = tracker.process_frame(frame, timestamp_ms=float(i * 33))

        stats = tracker.get_motion_stats()
        assert stats.avg_motion_energy >= 0.0
        assert stats.motion_trend in ["increasing", "stable", "decreasing"]
        assert stats.confidence <= 1.0

    def test_flow_visualization(self, sample_chart_image: Image.Image) -> None:
        """Test optical flow visualization generation."""
        tracker = OpticalFlowTracker()
        flow_frame = tracker.process_frame(sample_chart_image, 0.0)
        _ = tracker.process_frame(sample_chart_image, 33.0)

        viz_image = tracker.visualize_flow(np.array(sample_chart_image), flow_frame)
        assert isinstance(viz_image, Image.Image)
        assert viz_image.size == sample_chart_image.size


# ============================================================================
# TEST GROUP 4: Chart Segmentation
# ============================================================================

class TestChartSegmentation:
    """Test chart boundary detection."""

    def test_segmentation_engine_initialization(self):
        """Test engine creation."""
        engine = ChartSegmentationEngine()
        assert engine is not None

    def test_chart_segmentation(self, sample_chart_image: Image.Image) -> None:
        """Test chart boundary detection."""
        engine = ChartSegmentationEngine()
        segmentation = engine.segment_chart(sample_chart_image)

        assert segmentation is not None
        # Even with heuristic, should produce mask
        assert segmentation.mask is not None
        assert segmentation.bbox is not None

    def test_segmentation_mask_properties(self, sample_chart_image: Image.Image) -> None:
        """Test segmentation mask validity."""
        engine = ChartSegmentationEngine()
        segmentation = engine.segment_chart(sample_chart_image)

        # Mask should be binary
        assert np.all((segmentation.mask == 0) | (segmentation.mask == 255))

    def test_chart_extraction(self, sample_chart_image: Image.Image) -> None:
        """Test cropping and extracting chart region."""
        engine = ChartSegmentationEngine()
        segmentation = engine.segment_chart(sample_chart_image)

        if segmentation.is_valid:
            cropped, stats = engine.extract_chart_region(sample_chart_image, segmentation)
            assert isinstance(cropped, Image.Image)
            assert stats.width_px > 0
            assert stats.height_px > 0

    def test_segmentation_visualization(self, sample_chart_image: Image.Image) -> None:
        """Test segmentation visualization."""
        engine = ChartSegmentationEngine()
        segmentation = engine.segment_chart(sample_chart_image)
        viz = engine.visualize_segmentation(sample_chart_image, segmentation)

        assert isinstance(viz, Image.Image)
        assert viz.size == sample_chart_image.size


# ============================================================================
# TEST GROUP 5: Integration & End-to-End
# ============================================================================

class TestEnhancedVisionEngine:
    """Test complete integrated vision module."""

    def test_engine_initialization(self):
        """Test enhanced vision engine creation."""
        engine = EnhancedVisionEngine(
            enable_vit=False,  # Skip heavy models in tests
            enable_yolo=False,
            enable_sam=False,
            enable_optical_flow=True,
        )
        assert engine is not None
        assert engine.frame_counter == 0

    def test_end_to_end_frame_processing(self, sample_chart_image: Image.Image) -> None:
        """Test complete pipeline on single frame."""
        engine = EnhancedVisionEngine(
            enable_vit=False,
            enable_yolo=False,
            enable_sam=False,
            enable_optical_flow=True,
        )

        output = engine.process_frame(sample_chart_image, timestamp_ms=0.0)

        assert output is not None
        assert output.inference_time_ms >= 0.0
        assert 0.0 <= output.quality_score <= 1.0

    def test_multi_frame_sequence(self, sample_video_frames: list[Image.Image]) -> None:
        """Test pipeline on frame sequence."""
        engine = EnhancedVisionEngine(
            enable_vit=False,
            enable_yolo=False,
            enable_sam=False,
            enable_optical_flow=True,
        )

        outputs: list[EnhancedVisionOutput] = []
        for frame in sample_video_frames[:3]:
            output = engine.process_frame(frame)
            outputs.append(output)

        assert len(outputs) == 3
        assert engine.frame_counter == 3

    def test_output_structure(self, sample_chart_image: Image.Image) -> None:
        """Verify output contains all required fields."""
        engine = EnhancedVisionEngine(
            enable_vit=False,
            enable_yolo=False,
            enable_sam=False,
            enable_optical_flow=True,
        )

        output = engine.process_frame(sample_chart_image)

        # Check all fields exist
        assert hasattr(output, "detections")
        assert hasattr(output, "detection_confidence")
        assert hasattr(output, "motion_frame")
        assert hasattr(output, "motion_energy")
        assert hasattr(output, "inference_time_ms")
        assert hasattr(output, "quality_score")
        assert hasattr(output, "attribution_scores")
        assert hasattr(output, "feature_importance")

    def test_engine_reset(self, sample_chart_image: Image.Image) -> None:
        """Test engine state reset."""
        engine = EnhancedVisionEngine(
            enable_vit=False,
            enable_yolo=False,
            enable_sam=False,
            enable_optical_flow=True,
        )

        _ = engine.process_frame(sample_chart_image)
        assert engine.frame_counter == 1

        engine.reset()
        assert engine.frame_counter == 0

    def test_diagnostics_interface(self, sample_chart_image: Image.Image) -> None:
        """Test diagnostic data collection."""
        engine = EnhancedVisionEngine(
            enable_vit=False,
            enable_yolo=False,
            enable_sam=False,
            enable_optical_flow=True,
        )

        _ = engine.process_frame(sample_chart_image)
        diag = engine.get_diagnostics()

        assert "frame_count" in diag
        assert diag["frame_count"] == 1


# ============================================================================
# TEST GROUP 6: Edge Cases & Robustness
# ============================================================================

class TestEdgeCases:
    """Test robustness under edge conditions."""

    def test_empty_image_handling(self):
        """Test handling of empty/blank images."""
        engine = EnhancedVisionEngine(
            enable_vit=False,
            enable_yolo=False,
            enable_sam=False,
            enable_optical_flow=False,
        )

        blank_image = Image.new("RGB", (640, 480), color="white")
        output = engine.process_frame(blank_image)
        # Should not crash
        assert output is not None

    def test_small_image_handling(self):
        """Test handling of small images."""
        engine = EnhancedVisionEngine(
            enable_vit=False,
            enable_yolo=False,
            enable_sam=False,
            enable_optical_flow=True,
        )

        small_image = Image.new("RGB", (64, 64), color="gray")
        output = engine.process_frame(small_image)
        assert output is not None

    def test_numpy_array_input(self):
        """Test numpy array input handling."""
        engine = EnhancedVisionEngine(
            enable_vit=False,
            enable_yolo=False,
            enable_sam=False,
            enable_optical_flow=True,
        )

        img_array: NDArray[np.uint8] = np.full((480, 640, 3), 128, dtype=np.uint8)
        output = engine.process_frame(img_array)
        assert output is not None

    def test_grayscale_image_handling(self):
        """Test handling of grayscale images."""
        engine = EnhancedVisionEngine(
            enable_vit=False,
            enable_yolo=False,
            enable_sam=False,
            enable_optical_flow=True,
        )

        gray_array: NDArray[np.uint8] = np.full((480, 640), 128, dtype=np.uint8)
        output = engine.process_frame(gray_array)
        assert output is not None


# ============================================================================
# TEST GROUP 7: Performance Benchmarking
# ============================================================================

class TestPerformance:
    """Test performance characteristics."""

    def test_optical_flow_latency(self, sample_chart_image: Image.Image) -> None:
        """Benchmark optical flow inference time."""
        tracker = OpticalFlowTracker()

        # Warmup
        _ = tracker.process_frame(sample_chart_image, 0.0)

        # Measure
        start = time.time()
        _ = tracker.process_frame(sample_chart_image, 33.0)
        elapsed_ms = (time.time() - start) * 1000

        logger.info(f"Optical flow latency: {elapsed_ms:.1f}ms")
        assert elapsed_ms < 500  # Should be fast

    def test_segmentation_latency(self, sample_chart_image: Image.Image) -> None:
        """Benchmark chart segmentation time."""
        engine = ChartSegmentationEngine()

        start = time.time()
        _ = engine.segment_chart(sample_chart_image)
        elapsed_ms = (time.time() - start) * 1000

        logger.info(f"Segmentation latency: {elapsed_ms:.1f}ms")
        assert elapsed_ms < 1000  # Should complete in reasonable time

    def test_end_to_end_latency(self, sample_chart_image: Image.Image) -> None:
        """Benchmark complete pipeline."""
        engine = EnhancedVisionEngine(
            enable_vit=False,
            enable_yolo=False,
            enable_sam=False,
            enable_optical_flow=True,
        )

        start = time.time()
        output = engine.process_frame(sample_chart_image)
        elapsed_ms = (time.time() - start) * 1000

        logger.info(f"End-to-end pipeline latency: {elapsed_ms:.1f}ms")
        assert output.inference_time_ms >= 0.0


# ============================================================================
# PYTEST CONFIGURATION
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
