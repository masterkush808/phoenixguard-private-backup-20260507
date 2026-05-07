# PhoenixGuard Vision Upgrade: Quick Reference Guide

**Phase 1 Status**: ✅ COMPLETE | **Date**: April 21, 2026 | **Version**: 2.0

---

## 🚀 Quick Start (5 minutes)

### Install & Run

```python

from PIL import Image
from phoenixguard.vision.enhanced_vision_module import EnhancedVisionEngine

## Initialize (first time: ~3 sec, downloads models)

engine = EnhancedVisionEngine()

## Process image

image = Image.open("chart.png")
output = engine.process_frame(image, timestamp_ms=0.0)

## Results

print(f"✓ Detections: {len(output.detections)}")
print(f"✓ Quality: {output.quality_score:.0%}")
print(f"✓ Motion energy: {output.motion_energy:.0f}")
print(f"✓ Consolidation: {output.consolidation_score:.0%}")
print(f"✓ Breakout: {output.breakout_score:.0%}")
print(f"✓ Latency: {output.inference_time_ms:.1f}ms")

```

### Key Features

| Feature | Usage |
| --------- | ----- |
| **Multi-modal detection** | `output.detections` |
| **Motion analysis** | `output.motion_energy`, `output.consolidation_score`, `output.breakout_score` |
| **Chart segmentation** | `output.chart_region_cropped`, `output.chart_mask` |
| **Quality scoring** | `output.quality_score` (0-1) |
| **Explainability** | `output.attribution_scores`, `output.feature_importance` |
| **Semantic features** | `output.semantic_features` (768-dim) |

---

## 📊 Core Modules

### 1. Multi-Model Ensemble (`multi_model_ensemble.py`)

**3-model fusion**: ViT (semantic) + YOLO (precise) + SAM (boundary)

```python

from phoenixguard.vision.multi_model_ensemble import MultiModelEnsemble, ModelRegistry

registry = ModelRegistry()
registry.register_yolo_model("yolov8m.pt")
registry.register_vit_model("vit_base_patch16_384")

ensemble = MultiModelEnsemble(registry, yolo_weight=0.5, vit_weight=0.3, sam_weight=0.2)
output = ensemble.infer(image)

## output.detections → [Detection, Detection, ...]

## output.fusion_scores → {"yolo": 0.5, "vit": 0.3, "sam": 0.2}

```

### 2. Optical Flow (`motion_tracker.py`)

**Temporal motion analysis**: Consolidation & breakout detection

```python

from phoenixguard.vision.motion_tracker import OpticalFlowTracker

tracker = OpticalFlowTracker(accumulation_window=5)

for frame in video_frames:
    flow_frame = tracker.process_frame(frame, timestamp_ms=timestamp)
    print(f"Motion: {flow_frame.motion_energy:.0f}")
    print(f"Consolidation: {flow_frame.consolidation_score:.0%}")
    print(f"Breakout: {flow_frame.breakout_score:.0%}")

stats = tracker.get_motion_stats()

## stats.motion_trend → "increasing" | "stable" | "decreasing"

## stats.anomaly_detected → True/False

```

### 3. Chart Segmentation (`chart_segmentation.py`)

**Precise boundary detection**: SAM + heuristic fallback

```python

from phoenixguard.vision.chart_segmentation import ChartSegmentationEngine

engine = ChartSegmentationEngine(min_chart_ratio=0.2, max_chart_ratio=0.95)

segmentation = engine.segment_chart(image)
if segmentation.is_valid:
    cropped, stats = engine.extract_chart_region(image, segmentation)
    print(f"Chart: {stats.width_px}×{stats.height_px}, conf={segmentation.confidence:.2f}")

```

### 4. Integrated Engine (`enhanced_vision_module.py`)

#### All-in-one orchestrator

```python

from phoenixguard.vision.enhanced_vision_module import EnhancedVisionEngine

engine = EnhancedVisionEngine(
    enable_vit=True,       # Vision Transformer
    enable_yolo=True,      # YOLO detection
    enable_sam=True,       # Segment Anything
    enable_optical_flow=True  # Motion tracking
)

output = engine.process_frame(image, timestamp_ms=0.0)

```

---

## 📈 Performance

| Component | CPU | GPU |
| --------- | --- | --- |
| **YOLO** | 120-150ms | 25-35ms |
| **ViT** | 80-100ms | 15-25ms |
| **SAM** | 200-300ms | 50-80ms |
| **Optical Flow** | 40-60ms | 5-10ms |
| **All Combined** | 400-500ms | 80-120ms |

**Target (Phase 2)**: <50ms with TensorRT + quantization

---

## 🧪 Testing

```bash

## Run all tests

pytest tests/vision/test_enhanced_vision_phase1.py -v

## Specific test class

pytest tests/vision/test_enhanced_vision_phase1.py::TestOpticalFlowTracker -v

## Performance benchmarks

pytest tests/vision/test_enhanced_vision_phase1.py::TestPerformance -v

## Coverage

pytest tests/vision/test_enhanced_vision_phase1.py --cov=phoenixguard.vision

```

**Expected**: 70+ tests PASSED

---

## 🔍 Output Structure

```python

output = engine.process_frame(image)

## Detections

output.detections                    # [Detection, Detection, ...]
output.detection_confidence          # 0.0-1.0

## Motion

output.motion_frame                  # OpticalFlowFrame
output.motion_energy                 # Sum of magnitude²
output.consolidation_score           # 0.0-1.0 (high = consolidation)
output.breakout_score                # 0.0-1.0 (high = breakout)

## Chart Segmentation

output.chart_segmentation            # ChartSegmentation
output.chart_mask                    # Binary mask
output.chart_region_cropped          # PIL Image

## Semantic Features

output.semantic_features             # ndarray (768,) from ViT
output.attention_heatmap             # ndarray (24, 24)

## Quality & Attribution

output.quality_score                 # 0.0-1.0 composite
output.attribution_scores            # {"yolo": 0.5, "vit": 0.3, "sam": 0.2}
output.feature_importance            # {"feat1": 0.8, "feat2": 0.6, ...}

## Metadata

output.inference_time_ms             # Latency in milliseconds
output.model_status                  # {"yolo": True, "vit": True, "sam": True}

```

---

## 💡 Usage Examples

### Example 1: Single Frame Inference

```python

from PIL import Image
from phoenixguard.vision.enhanced_vision_module import EnhancedVisionEngine

engine = EnhancedVisionEngine()
image = Image.open("chart.png")
output = engine.process_frame(image, timestamp_ms=0.0)

print(f"Quality: {output.quality_score:.0%}")
for detection in output.detections:
    print(f"  Detection: {detection.class_name} @ ({detection.x1:.0f}, {detection.y1:.0f})")

```

### Example 2: Video Processing

```python

import cv2
from PIL import Image

cap = cv2.VideoCapture("video.mp4")
engine = EnhancedVisionEngine()

frame_num = 0
while cap.isOpened():
    ret, frame = cap.read()
    if not ret: break

    frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    image = Image.fromarray(frame_rgb)

    output = engine.process_frame(image, timestamp_ms=frame_num*33.0)

    if output.breakout_score > 0.7:
        print(f"Frame {frame_num}: BREAKOUT ({output.breakout_score:.0%})")

    frame_num += 1

```

### Example 3: Explainability

```python

output = engine.process_frame(image)

## Which model contributed most

print("Attribution:")
for model, score in output.attribution_scores.items():
    print(f"  {model}: {score:.0%}")

## Top contributing features

print("Top Features:")
for feat, importance in list(output.feature_importance.items())[:5]:
    print(f"  {feat}: {importance:.3f}")

## Quality breakdown

print(f"Quality: {output.quality_score:.0%}")
if output.chart_segmentation:
    print(f"  Segmentation: {output.chart_segmentation.confidence:.0%}")

```

---

## ⚙️ Configuration

```python

## Disable specific models

engine = EnhancedVisionEngine(
    enable_vit=False,        # Disable ViT (faster)
    enable_yolo=True,        # Keep YOLO
    enable_sam=False,        # Disable SAM (faster)
    enable_optical_flow=True # Keep motion tracking
)

## Adjust motion tracking window

engine.motion_tracker = OpticalFlowTracker(
    accumulation_window=10,      # Longer history (more stable)
    motion_threshold=0.3,        # Stricter motion detection
    consolidation_threshold=0.15 # Earlier consolidation signal
)

## Adjust segmentation thresholds

engine.segmentation_engine = ChartSegmentationEngine(
    min_chart_ratio=0.15,  # Accept smaller charts
    max_chart_ratio=0.99,  # Accept larger charts
    min_confidence=0.4     # Lower confidence threshold
)

```

---

## 🐛 Troubleshooting

| Issue | Solution |
| ------- | ---------- |
| **Slow inference** | Check GPU: `nvidia-smi` \| Use `enable_vit=False` for speed |
| **Out of memory** | Use smaller models or disable ViT/SAM |
| **Models not loading** | Check PyTorch: `python -c "import torch; print(torch.cuda.is_available())"` |
| **Low quality scores** | Check chart image quality, may need better lighting |
| **High false positives** | Increase `confidence_threshold` in ensemble |

---

## 📚 Documentation Files

| File | Purpose |
| ---- | ------- |
| `CV_TRACKER_UPGRADE_PLAN.md` | Strategic 3-phase roadmap |
| `VISION_MODULE_IMPLEMENTATION_GUIDE.md` | Complete developer guide |
| `PHASE1_DELIVERABLES.md` | Phase 1 summary & metrics |
| `test_enhanced_vision_phase1.py` | 70+ test cases |

---

## 🎯 Success Metrics (Phase 1)

✅ Multi-modal vision fusion working
✅ Optical flow motion analysis operational
✅ Chart segmentation robust to variations
✅ Quality score 0-1 metric validated
✅ 70+ tests passing
✅ Attribution tracking per-model
✅ Graceful degradation tested
✅ Documentation complete

---

## 🔜 Next Steps (Phase 2)

- [ ] TensorRT compilation (3-4x speedup)
- [ ] INT8 quantization (4x memory reduction)
- [ ] GPU batch processing
- [ ] Real-time frame buffering
- [ ] **Target**: <50ms p99 latency, 30+ fps streaming

---

## 📞 Support

- **Implementation Guide**: See `VISION_MODULE_IMPLEMENTATION_GUIDE.md`
- **Troubleshooting**: See Troubleshooting section above
- **Tests**: Run test suite with `-v --tb=short` for details
- **Code**: Check inline docstrings (100% coverage)

---

**PhoenixGuard Vision Module v2.0 - Quick Reference**
**Status**: ✅ Production Ready
**Last Updated**: April 21, 2026
