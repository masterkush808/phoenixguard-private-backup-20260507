"""
PhoenixGuard Optical Flow Motion Detector
==========================================
Adds temporal signal by tracking pixel-level motion across frames.

Provides:
  - Candlestick movement velocity
  - Consolidation zone detection
  - Breakout signal enhancement
  - Motion-based feature extraction
  - Frame-to-frame consistency analysis

Industry application: Differentiates volatile/choppy price action from
genuine directional moves using optical flow features.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import cv2
import numpy as np
from numpy.typing import NDArray
from PIL import Image


logger = logging.getLogger(__name__)


@dataclass
class OpticalFlowFrame:
    """Single frame's optical flow analysis."""
    frame_id: int
    timestamp_ms: float
    flow: NDArray[np.float32]  # (H, W, 2) optical flow vectors
    magnitude: NDArray[np.float32]  # (H, W) flow magnitude
    angle: NDArray[np.float32]  # (H, W) flow direction in radians
    motion_mask: NDArray[np.uint8]  # Binary mask of significant motion
    consolidation_score: float  # 0.0-1.0, high = low motion
    breakout_score: float  # 0.0-1.0, high = accelerating motion
    dominant_direction: tuple[float, float]  # (vx, vy) of primary motion
    motion_energy: float  # Sum of magnitude^2 (total motion)
    consistency: float  # 0.0-1.0, how similar to previous frame
    chart_region_only: bool  # Whether motion was isolated to chart area


@dataclass
class OpticalFlowStats:
    """Aggregated statistics from motion sequence."""
    avg_motion_energy: float
    motion_trend: str  # 'increasing' | 'stable' | 'decreasing'
    consolidation_count: int  # Frames with low motion
    breakout_count: int  # Frames with accelerating motion
    dominant_directions: list[tuple[float, float]]  # History of directions
    anomaly_detected: bool  # Sudden motion spike
    confidence: float  # How reliable are these measurements


class OpticalFlowTracker:
    """
    Multi-frame optical flow analysis for motion-augmented trading signals.

    Algorithm: Dense optical flow (DIS - Dense Inverse Search)
    - Fast: ~50ms per frame on GPU
    - Robust: Handles illumination changes, chart overlays
    - Sensitive: Detects sub-pixel motion (chart scrolling)

    Features:
      1. Magnitude-based motion energy (which pixels are moving most)
      2. Angle-based direction (consistent vs. random motion)
      3. Consolidation detection (entropy-based, low motion = consolidation)
      4. Breakout detection (acceleration, motion energy increase)
      5. Chart-region isolation (motion only in candlestick area)
    """

    def __init__(
        self,
        accumulation_window: int = 5,
        motion_threshold: float = 0.5,
        consolidation_threshold: float = 0.2,
        chart_region_roi: Optional[tuple[int, int, int, int]] = None,
    ):
        """
        Args:
            accumulation_window: Number of frames to aggregate stats over
            motion_threshold: Magnitude threshold to mark pixel as "moving"
            consolidation_threshold: Below this energy = consolidation zone
            chart_region_roi: (x1, y1, x2, y2) crop for chart-only analysis
        """
        self.accumulation_window = accumulation_window
        self.motion_threshold = motion_threshold
        self.consolidation_threshold = consolidation_threshold
        self.chart_region_roi = chart_region_roi

        # DIS optical flow detector
        self.flow_detector = cv2.DISOpticalFlow_create(cv2.DISOPTICAL_FLOW_PRESET_MEDIUM)
        
        # Frame history
        self.frame_history: list[OpticalFlowFrame] = []
        self.prev_gray: Optional[NDArray[np.uint8]] = None
        self.frame_counter = 0

    def process_frame(
        self,
        frame: Image.Image | NDArray[np.uint8],
        timestamp_ms: float = 0.0,
    ) -> OpticalFlowFrame:
        """
        Process single frame and return optical flow analysis.

        Args:
            frame: PIL Image or numpy array (HxWx3 or HxWx4)
            timestamp_ms: Timestamp for this frame (for debugging)

        Returns:
            OpticalFlowFrame with complete motion analysis
        """
        # Convert to grayscale
        if isinstance(frame, Image.Image):
            frame_array = np.array(frame)
        else:
            frame_array = frame

        if frame_array.ndim == 3:
            if frame_array.shape[2] == 4:
                frame_array = frame_array[..., :3]
            gray = cv2.cvtColor(frame_array, cv2.COLOR_RGB2GRAY)
        else:
            gray = frame_array

        # Initialize previous frame
        if self.prev_gray is None:
            self.prev_gray = gray
            # Return dummy frame
            h, w = gray.shape
            return OpticalFlowFrame(
                frame_id=self.frame_counter,
                timestamp_ms=timestamp_ms,
                flow=np.zeros((h, w, 2), dtype=np.float32),
                magnitude=np.zeros((h, w), dtype=np.float32),
                angle=np.zeros((h, w), dtype=np.float32),
                motion_mask=np.zeros((h, w), dtype=np.uint8),
                consolidation_score=1.0,  # First frame = consolidation
                breakout_score=0.0,
                dominant_direction=(0.0, 0.0),
                motion_energy=0.0,
                consistency=1.0,  # Perfect match with "previous"
                chart_region_only=False,
            )

        # Compute optical flow
        flow = self.flow_detector.calc(self.prev_gray, gray, None)

        # Extract flow components
        magnitude, angle = cv2.cartToPolar(flow[..., 0], flow[..., 1])

        # Create motion mask
        motion_mask = (magnitude > self.motion_threshold).astype(np.uint8) * 255

        # Compute motion metrics
        motion_energy = float(np.sum(magnitude ** 2))
        consolidation_score = self._compute_consolidation_score(magnitude)
        breakout_score = self._compute_breakout_score(magnitude, motion_energy)
        dominant_direction = self._get_dominant_direction(flow)
        consistency = self._compute_frame_consistency(magnitude)

        # Isolate chart region if ROI provided
        chart_region_only = False
        if self.chart_region_roi is not None:
            x1, y1, x2, y2 = self.chart_region_roi
            chart_motion = magnitude[y1:y2, x1:x2].sum()
            total_motion = magnitude.sum()
            chart_region_only = (chart_motion / (total_motion + 1e-6)) > 0.8

        flow_frame = OpticalFlowFrame(
            frame_id=self.frame_counter,
            timestamp_ms=timestamp_ms,
            flow=flow,
            magnitude=magnitude,
            angle=angle,
            motion_mask=motion_mask,
            consolidation_score=consolidation_score,
            breakout_score=breakout_score,
            dominant_direction=dominant_direction,
            motion_energy=motion_energy,
            consistency=consistency,
            chart_region_only=chart_region_only,
        )

        # Store in history and maintain window
        self.frame_history.append(flow_frame)
        if len(self.frame_history) > self.accumulation_window:
            self.frame_history.pop(0)

        self.prev_gray = gray
        self.frame_counter += 1

        return flow_frame

    def _compute_consolidation_score(self, magnitude: NDArray[np.float32]) -> float:
        """
        Score consolidation zone (low motion variance).
        1.0 = perfect consolidation, 0.0 = high motion.
        """
        # Entropy-like measure: uniform low motion = consolidation
        motion_entropy = -np.sum(
            np.clip(magnitude / (magnitude.max() + 1e-6), 0, 1) * np.log(np.clip(magnitude / (magnitude.max() + 1e-6), 0, 1) + 1e-6)
        )
        max_entropy = magnitude.size * np.log(magnitude.size)
        normalized_entropy = motion_entropy / (max_entropy + 1e-6)

        # Low entropy + low average motion = consolidation
        avg_motion = magnitude.mean()
        consolidation = 1.0 - min(1.0, avg_motion / self.consolidation_threshold)
        consolidation = consolidation * (1.0 - normalized_entropy)

        return float(np.clip(consolidation, 0, 1))

    def _compute_breakout_score(self, magnitude: NDArray[np.float32], motion_energy: float) -> float:
        """
        Score breakout zone (accelerating motion).
        1.0 = strong breakout, 0.0 = no breakout.
        """
        # Compare to previous frames
        if len(self.frame_history) < 2:
            return 0.0

        prev_energy = self.frame_history[-1].motion_energy
        energy_delta = motion_energy - prev_energy

        # Acceleration: energy increasing
        if energy_delta > 0:
            energy_acceleration = min(1.0, energy_delta / (prev_energy + 1e-6))
        else:
            energy_acceleration = 0.0

        # High motion in narrow region = breakout
        high_motion_pixels = np.sum(magnitude > self.motion_threshold * 2)
        motion_concentration = high_motion_pixels / magnitude.size

        breakout_score = (energy_acceleration * 0.6) + (motion_concentration * 0.4)
        return float(np.clip(breakout_score, 0, 1))

    def _get_dominant_direction(self, flow: NDArray[np.float32]) -> tuple[float, float]:
        """Compute primary direction of motion (average of high-motion pixels)."""
        magnitude = np.sqrt(flow[..., 0] ** 2 + flow[..., 1] ** 2)
        threshold = np.percentile(magnitude, 75)  # Top 25% motion pixels

        valid = magnitude > threshold
        if not valid.any():
            return (0.0, 0.0)

        avg_vx = float(flow[valid, 0].mean())
        avg_vy = float(flow[valid, 1].mean())
        return (avg_vx, avg_vy)

    def _compute_frame_consistency(self, magnitude: NDArray[np.float32]) -> float:
        """
        Measure consistency with previous frame.
        1.0 = identical motion pattern, 0.0 = completely different.
        """
        if len(self.frame_history) < 1:
            return 1.0

        prev_magnitude = self.frame_history[-1].magnitude

        # Correlation between magnitude patterns
        if magnitude.size == 0:
            return 0.0

        norm_prev = (prev_magnitude - prev_magnitude.mean()) / (prev_magnitude.std() + 1e-6)
        norm_curr = (magnitude - magnitude.mean()) / (magnitude.std() + 1e-6)

        correlation = np.mean(norm_prev * norm_curr)
        return float(np.clip(correlation, -1, 1))

    def get_motion_stats(self) -> OpticalFlowStats:
        """Aggregate statistics from accumulated frames."""
        if not self.frame_history:
            return OpticalFlowStats(
                avg_motion_energy=0.0,
                motion_trend="stable",
                consolidation_count=0,
                breakout_count=0,
                dominant_directions=[],
                anomaly_detected=False,
                confidence=0.0,
            )

        motion_energies = [f.motion_energy for f in self.frame_history]
        consolidation_scores = [f.consolidation_score for f in self.frame_history]
        breakout_scores = [f.breakout_score for f in self.frame_history]
        directions = [f.dominant_direction for f in self.frame_history]

        # Motion trend
        if len(motion_energies) > 1:
            energy_diff = motion_energies[-1] - motion_energies[0]
            if energy_diff > 0.1:
                motion_trend = "increasing"
            elif energy_diff < -0.1:
                motion_trend = "decreasing"
            else:
                motion_trend = "stable"
        else:
            motion_trend = "stable"

        # Anomaly detection (sudden spike)
        energy_mean = np.mean(motion_energies) if motion_energies else 0.0
        energy_std = np.std(motion_energies) if len(motion_energies) > 1 else 0.0
        anomaly = False
        if len(motion_energies) > 1 and energy_std > 0:
            if abs(motion_energies[-1] - energy_mean) > 3 * energy_std:
                anomaly = True

        return OpticalFlowStats(
            avg_motion_energy=float(np.mean(motion_energies)),
            motion_trend=motion_trend,
            consolidation_count=sum(1 for s in consolidation_scores if s > 0.7),
            breakout_count=sum(1 for s in breakout_scores if s > 0.7),
            dominant_directions=directions,
            anomaly_detected=anomaly,
            confidence=min(1.0, len(self.frame_history) / self.accumulation_window),
        )

    def visualize_flow(self, frame: NDArray[np.uint8], flow_frame: OpticalFlowFrame) -> Image.Image:
        """
        Create visualization of optical flow vectors.

        Returns PIL Image with flow vectors overlaid on input frame.
        """
        flow = flow_frame.flow
        magnitude = flow_frame.magnitude

        # Create output image
        hsv = np.zeros((frame.shape[0], frame.shape[1], 3), dtype=np.uint8)
        hsv[..., 1] = 255

        # Hue based on direction
        hsv[..., 0] = np.uint8(179 * (flow_frame.angle + np.pi) / (2 * np.pi))

        # Saturation/Value based on magnitude
        hsv[..., 2] = cv2.normalize(magnitude, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)

        rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)

        # Overlay on original frame (50/50 blend)
        output = cv2.addWeighted(frame, 0.5, bgr, 0.5, 0)

        return Image.fromarray(cv2.cvtColor(output, cv2.COLOR_BGR2RGB))

    def reset(self):
        """Clear history and start fresh."""
        self.frame_history = []
        self.prev_gray = None
        self.frame_counter = 0
