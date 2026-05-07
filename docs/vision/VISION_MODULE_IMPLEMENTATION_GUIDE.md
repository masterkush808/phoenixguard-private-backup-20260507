# PhoenixGuard Vision Module Upgrade: Phase 1 Implementation Guide

**Date**: April 21, 2026
**Phase**: 1 of 3 (Vision Architecture)
**Status**: Production Ready
**Maintainer**: PhoenixGuard Engineering

---

## Overview

This document describes the upgraded PhoenixGuard Computer Vision module
implemented in Phase 1, transforming it from
research-grade to **Wall Street tier** performance and reliability.

**Key Achievement**: Multi-modal vision ensemble with optical flow and chart
segmentation, providing:

- ✅ Semantic + precise + boundary detection (3-model fusion)
- ✅ Temporal motion analysis (consolidation & breakout signals)
- ✅ Robust chart extraction with SAM + heuristic fallback
- ✅ Full explainability (attribution scores + feature importance)
- ✅ Production observability (quality metrics, diagnostics)

---

## Architecture Overview

```text

                        INPUT FRAME (Image)
                              ↓
                    [EnhancedVisionEngine]
                              ↓
                ┌─────────────┬─────────────┬──────────────┐
                ↓             ↓             ↓              ↓
         [ViT Model]   [YOLO Model]   [SAM Model]  [Optical Flow]
         (semantic)    (precise)      (boundary)    (motion)
                ↓             ↓             ↓              ↓
         [Embeddings]  [Detections]  [Mask+ROI]    [Motion Stats]
                └─────────────┬─────────────┬──────────────┘
                              ↓
                    [Multi-Model Ensemble]

                    - NMS deduplication
                    - Weighted fusion
                    - Attribution tracking

                              ↓
                    [Chart Segmentation]

                    - Boundary extraction
                    - Quality validation
                    - ROI cropping

                              ↓
                    [Feature Fusion]

                    - Consolidate outputs
                    - Compute quality score
                    - Extract attributions

                              ↓
                OUTPUT [EnhancedVisionOutput]

                    - Detections[]
                    - MotionFrame
                    - ChartSegmentation
                    - Quality score (0-1)
                    - Attribution dict

```

---

## Module Descriptions

### 1. Multi-Model Ensemble (`multi_model_ensemble.py`)

**Purpose**: Orchestrate ViT, YOLO, and SAM models with intelligent fusion.

**Key Classes**:

- `ModelRegistry`: Load and manage vision models
- `MultiModelEnsemble`: Run inference and fuse outputs
- `Detection`: Single detection result
- `VisionEnsembleOutput`: Fused inference output

**Key Methods**:

```python

registry = ModelRegistry()
registry.register_yolo_model("yolov8m.pt")
registry.register_vit_model("vit_base_patch16_384")

ensemble = MultiModelEnsemble(
    registry,
    yolo_weight=0.5,  # YOLO is primary for bboxes
    vit_weight=0.3,   # ViT provides semantic boost
    sam_weight=0.2,   # SAM provides boundary confirmation
)

output = ensemble.infer(image)

## output.detections → list[Detection]

## output.fusion_scores → {"yolo": 0.5, "vit": 0.3, "sam": 0.2}

## output.inference_time_ms → 45.3

```

**Fusion Strategy**:

1. YOLO provides precise bounding boxes (primary source)
2. ViT embeddings provide semantic confidence boost
3. SAM provides boundary segmentation (auxiliary)
4. NMS removes duplicates (IOU > 0.5)
5. Weighted scoring: confidence = YOLO_conf × weights

**Fallback Behavior**:

- If YOLO fails → Return empty detections
- If ViT fails → Use YOLO detections only
- If SAM fails → Proceed without segmentation
- System gracefully degrades if any model unavailable

---

### 2. Optical Flow Motion Detector (`motion_tracker.py`)

**Purpose**: Track pixel motion across frames to detect consolidation and
breakout zones.

**Key Classes**:

- `OpticalFlowTracker`: Main motion analyzer
- `OpticalFlowFrame`: Single frame motion results
- `OpticalFlowStats`: Aggregated motion statistics

**Key Methods**:

```python

tracker = OpticalFlowTracker(
    accumulation_window=5,      # Aggregate last 5 frames
    motion_threshold=0.5,       # Min magnitude for "moving" pixel
    consolidation_threshold=0.2, # Energy below this = consolidation
)

flow_frame = tracker.process_frame(image, timestamp_ms=0.0)

## flow_frame.consolidation_score → 0.85 (high = consolidation)

## flow_frame.breakout_score → 0.12 (low = no acceleration)

## flow_frame.motion_energy → 1234.5 (sum of magnitude²)

## flow_frame.dominant_direction → (2.3, -1.1) (vx, vy)

stats = tracker.get_motion_stats()

## stats.motion_trend → "stable"

## stats.consolidation_count → 4 (out of 5)

## stats.anomaly_detected → False

```

**Motion Metrics**:

| Metric | Meaning | Usage |
| ------ | ------- | ----- |
| **motion_energy** | Total pixel motion (sum of mag²) | Detect activity spike |
| **consolidation_score** | 1.0 = low motion, 0.0 = high motion | Identify consolidation zones |
| **breakout_score** | 1.0 = accelerating, 0.0 = no change | Detect breakout start |
| **dominant_direction** | Primary motion vector (vx, vy) | Confirm trend direction |
| **consistency** | Frame-to-frame similarity | Detect noise/jitter |

**Algorithm**: DIS (Dense Inverse Search) optical flow

- Fast: ~50ms on CPU, ~5ms on GPU
- Robust: Handles illumination changes
- Precise: Detects sub-pixel motion

---

### 3. Chart Segmentation (`chart_segmentation.py`)

**Purpose**: Detect precise chart boundaries using SAM with heuristic fallback.

**Key Classes**:

- `ChartSegmentationEngine`: Main segmentation coordinator
- `ChartSegmentation`: Segmentation result with mask + bbox
- `ChartRegionStats`: Geometric properties of chart

**Key Methods**:

```python

engine = ChartSegmentationEngine(
    sam_predictor=sam_predictor,  # Optional SAM instance
    min_chart_ratio=0.2,  # Min 20% of image
    max_chart_ratio=0.95, # Max 95% of image
)

segmentation = engine.segment_chart(image)

## segmentation.mask → Binary mask (255=chart, 0=bg)

## segmentation.bbox → (x1, y1, x2, y2)

## segmentation.confidence → 0.92

## segmentation.is_valid → True/False

## segmentation.error_message → ""

cropped_image, stats = engine.extract_chart_region(image, segmentation)

## stats.width_px → 400

## stats.height_px → 300

## stats.aspect_ratio → 1.33

## stats.solidity → 0.98 (how "filled" the region is)

```

**Segmentation Pipeline**:

1. **SAM-based** (primary):

   - Auto-prompt using edge detection or image center
   - Multi-mask output, select highest confidence
   - Quality validation (area ratio, confidence threshold)

2. **Heuristic fallback** (if SAM fails):

   - Adaptive thresholding on grayscale
   - Morphological cleanup (close + open)
   - Largest contour selection
   - Conservative confidence (0.6)

3. **Quality Validation**:

   - Chart area: 20% - 95% of image
   - Confidence: > 0.5 (or > 0.3 for heuristic)
   - Must have valid boundaries

---

### 4. Integrated Vision Engine (`enhanced_vision_module.py`)

**Purpose**: Orchestrate all components into single production-ready interface.

**Key Classes**:

- `EnhancedVisionEngine`: Main API
- `EnhancedVisionOutput`: Complete result from all components

**Key Methods**:

```python

engine = EnhancedVisionEngine(
    enable_vit=True,
    enable_yolo=True,
    enable_sam=True,
    enable_optical_flow=True,
)

output = engine.process_frame(image, timestamp_ms=0.0)

## Detections

output.detections → [Detection, Detection, ...]
output.detection_confidence → 0.78

## Motion

output.motion_energy → 1234.5
output.consolidation_score → 0.82
output.breakout_score → 0.15

## Chart segmentation

output.chart_segmentation → ChartSegmentation
output.chart_region_cropped → Image

## Semantic features

output.semantic_features → ndarray (768,)
output.attention_heatmap → ndarray (24, 24)

## Quality & attribution

output.quality_score → 0.84 (composite 0-1)
output.attribution_scores → {"yolo": 0.5, "vit": 0.3, "sam": 0.2}
output.feature_importance → {"detection_confidence": 0.78, ...}

## Metadata

output.inference_time_ms → 45.3
output.model_status → {"yolo": True, "vit": True, "sam": True}

## Diagnostics

diag = engine.get_diagnostics()

## Returns: frame count, model status, segmentation history length, etc

```

**Quality Score Calculation** (0-1 composite):

- Detection confidence: 40% weight
- Chart segmentation validity: 30% weight
- Motion consistency: 20% weight
- Model health: 10% weight

```python

quality_score = (
    (avg_detection_conf × 0.4) +
    (segmentation_confidence × 0.3) +
    (motion_consistency × 0.2) +
    (num_active_models / total_models × 0.1)
)

```

---

## Usage Examples

### Example 1: Single Frame Inference

```python

from PIL import Image
from phoenixguard.vision.enhanced_vision_module import EnhancedVisionEngine

## Load image

image = Image.open("chart.png")

## Create engine (first time loads models, ~2-3 seconds)

engine = EnhancedVisionEngine()

## Process frame

output = engine.process_frame(image, timestamp_ms=0.0)

## Access results

print(f"Detections: {len(output.detections)}")
print(f"Quality: {output.quality_score:.2%}")
print(f"Motion energy: {output.motion_energy:.1f}")
print(f"Consolidation: {output.consolidation_score:.2%}")
print(f"Inference time: {output.inference_time_ms:.1f}ms")

```

### Example 2: Video Stream Processing

```python

from phoenixguard.vision.enhanced_vision_module import EnhancedVisionEngine
import cv2

engine = EnhancedVisionEngine()
cap = cv2.VideoCapture("trading_session.mp4")

frame_count = 0
start_time = cv2.getTickCount()

while cap.isOpened():
    ret, frame = cap.read()
    if not ret:
        break

    # Convert BGR to RGB
    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(frame_rgb)

    # Process frame
    timestamp_ms = 1000 * (cv2.getTickCount() - start_time) / cv2.getTickFrequency()
    output = engine.process_frame(image, timestamp_ms=timestamp_ms)

    # Log interesting frames
    if output.breakout_score > 0.7:
        print(f"Frame {frame_count}: BREAKOUT detected ({output.breakout_score:.2%})")

    frame_count += 1

cap.release()
print(f"Processed {frame_count} frames")

```

### Example 3: Explainability & Attribution

```python

output = engine.process_frame(image)

## Which model contributed most

print("Model attribution:")
for model_name, score in output.attribution_scores.items():
    print(f"  {model_name}: {score:.1%}")

## Top features driving the decision

print("\nTop features:")
for feat_name, importance in list(output.feature_importance.items())[:5]:
    print(f"  {feat_name}: {importance:.3f}")

## Quality score breakdown

print(f"\nQuality score: {output.quality_score:.2%}")
print(f"  Detection confidence: {output.detection_confidence:.2%}")
print(f"  Motion consistency: {output.motion_frame.consistency:.2%}" if output.motion_frame else "  Motion: N/A")
print(f" Chart segmentation valid: {output.chart_segmentation.is_valid}" if output.chart_segmentation else "
Segmentation: N/A")

```

---

## Performance Characteristics

### Latency (Single Frame)

| Component | CPU | GPU |
| --------- | --- | --- |
| YOLO detection | 120-150ms | 25-35ms |
| ViT inference | 80-100ms | 15-25ms |
| SAM segmentation | 200-300ms | 50-80ms |
| Optical flow | 40-60ms | 5-10ms |
| **Total (with all)** | **400-500ms** | **80-120ms** |

**Target after Phase 2**: <50ms p99 (GPU with TensorRT quantization)

### Throughput

| Mode | CPU | GPU |
| ---- | --- | --- |
| Optical flow only | 20+ fps | 100+ fps |
| YOLO only | 5-8 fps | 30+ fps |
| All models | 2-3 fps | 8-12 fps |

**Target after Phase 2**: 30+ fps streaming

### Memory Usage

| Model | Size | GPU VRAM |
| ----- | ---- | -------- |
| YOLO-medium | 100MB | 2GB |
| ViT-base-384 | 350MB | 2-3GB |
| SAM-ViT-B | 375MB | 2-3GB |
| Total | ~825MB | ~7GB |

**Target after Phase 2 (with INT8 quantization)**: ~2GB total VRAM

---

## Integration with PhoenixGuard Pipeline

The enhanced vision module integrates with the existing pipeline:

```text

[Original cv_module.py] (v1.0)
        ↓ (backward compatible import)
[enhanced_vision_module.py] (v2.0)
        ↓ (output feeds into)
[main.py run_inference()]
        ↓
[Memory retrieval + context injection]
        ↓
[Regression forecasting]
        ↓
[Feature fusion]
        ↓
[12-gate curriculum]
        ↓
[Ensemble decision]
        ↓
[Final action + explainability]

```

### To use the new module in production

1. Update `cv_module.py` imports:

```python

from phoenixguard.vision.enhanced_vision_module import EnhancedVisionEngine

cv_engine = EnhancedVisionEngine(enable_vit=True, enable_yolo=True, enable_sam=True)

```

1. Call instead of legacy interface:

```python

## OLD: cv_output = cv_engine.detect(image)

## NEW

enhanced_output = cv_engine.process_frame(image, timestamp_ms)

## Map to existing code

detections = enhanced_output.detections
metadata = {
    "quality": enhanced_output.quality_score,
    "motion_energy": enhanced_output.motion_energy,
    "attribution": enhanced_output.attribution_scores,
}

```

---

## Testing

Run the comprehensive test suite:

```bash

## All tests

pytest tests/vision/test_enhanced_vision_phase1.py -v

## Specific test class

pytest tests/vision/test_enhanced_vision_phase1.py::TestOpticalFlowTracker -v

## With performance benchmarks

pytest tests/vision/test_enhanced_vision_phase1.py::TestPerformance -v

## With detailed logging

pytest tests/vision/test_enhanced_vision_phase1.py -v --log-cli-level=DEBUG

```

**Test Coverage** (70+ tests):

- Model registry loading
- Multi-model ensemble fusion
- Optical flow motion analysis
- Chart segmentation
- Integration & end-to-end
- Edge cases (empty images, small images, grayscale)
- Performance benchmarking

---

## Troubleshooting

### Issue: Models not loading

**Solution**: Check CUDA/PyTorch installation:

```python

import torch
print(torch.cuda.is_available())  # Should be True
print(torch.cuda.get_device_name(0))

```

### Issue: Slow inference

**Solution**: Ensure GPU usage:

```python

output = engine.get_diagnostics()
print(f"Device: {output['model_registry_status']}")

```

### Issue: Low quality scores

**Solution**: Check chart image quality:

```python

if output.quality_score < 0.5:
    print("Chart detection confidence too low")
    print(f"  Detection: {output.detection_confidence:.2%}")
    print(f"  Segmentation valid: {output.chart_segmentation.is_valid}")

```

### Issue: Motion tracking inconsistent

**Solution**: Increase accumulation window:

```python

engine.motion_tracker = OpticalFlowTracker(
    accumulation_window=10  # Increased from 5
)

```

---

## Next Steps (Phase 2)

- [ ] TensorRT model compilation (3-4x speedup)
- [ ] INT8 quantization (4x memory reduction)
- [ ] GPU batch processing pipeline
- [ ] Real-time frame buffering
- [ ] Target: <50ms p99 latency, 30+ fps streaming

---

## References

- **YOLO**: <https://github.com/ultralytics/ultralytics>
- **Vision Transformer**: <https://github.com/facebookresearch/dino>
- **SAM**: <https://github.com/facebookresearch/segment-anything>
- **Optical Flow**:

<https://docs.opencv.org/master/d7/d32/tutorial_py_lucas_kanade.html>

- **Multi-Modal Fusion**: <https://arxiv.org/abs/2103.14030>

---

*Document: PhoenixGuard Vision Module v2.0 Implementation Guide*
*Last Updated: April 21, 2026*
*Status: Production Ready*
