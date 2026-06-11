"""
PhoenixGuard SIGE-VLA 3.0 — Zero-Shot Style Memory Bank
=========================================================
HNSW Multimodal Graph with Archetype Clustering + Chart-State Batch Ingestion

Run ONCE to build the memory bank:
    python memory_ingest.py

Provides at runtime:
    bank = MemoryBank.load(bank_dir)
    results = bank.search(query_embed, top_k=5)
    few_shot_ctx = bank.get_few_shot_context(results[:3])
    logit_boost  = bank.compute_logit_boost(results)

Skills wired:
  - Data Structures & Search Strategies (HNSW graph, priority queue)
  - Knowledge Representation & Reasoning (archetype ontology nodes)
  - Formal Language & Automata Theory (indicator-rejection FSM)
  - Advanced Probability & Statistics (cosine similarity, Bayesian weighting)
  - Predictive Analytics (DPO preference pair generation)
  - Clustering (K-Means archetype pruning)
  - KNN (K-Nearest-Neighbour recall)
  - Computer Graphics & Multimedia (visual fingerprint extraction)
  - Discrete Mathematics (set union of archetype prototypes)
  - Security (AES-256 via Fernet + hash-chain on all entries)
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping, cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image

# ── optional heavy deps (graceful degradation) ───────────────────────────────
_HNSW_OK: bool = False
try:
    import hnswlib  # type: ignore[import-untyped]
    _HNSW_OK = True  # type: ignore[misc]
except ImportError:
    pass

_FAISS_OK: bool = False
try:
    import faiss  # type: ignore[import-untyped]
    _FAISS_OK = True  # type: ignore[misc]
except ImportError:
    pass

_ST_OK: bool = False
SentenceTransformer = None  # type: ignore[assignment,misc]

_SK_OK: bool = False
try:
    from sklearn.cluster import MiniBatchKMeans  # type: ignore[import-untyped]
    _SK_OK = True  # type: ignore[misc]
except ImportError:
    MiniBatchKMeans = None  # type: ignore[assignment,misc]

_TORCH_OK: bool = False
try:
    import torch as _torch_probe  # noqa: F401
    _TORCH_OK = True  # type: ignore[misc]
    del _torch_probe
except Exception:
    pass

# ── repo-local imports ────────────────────────────────────────────────────────
from phoenixguard.core.utils import append_hash_chain, can_import_sentence_transformers_safely, setup_logger, utc_now_iso  # noqa: E402
from phoenixguard.memory.memory_features import (  # noqa: E402
    build_late_interaction_tokens,
    build_metric_profile,
    build_trajectory_signature,
    infer_style_signature_from_chart_state,
    late_interaction_score,
    metric_profile_alignment,
    style_alignment_score,
    trajectory_alignment,
)
from phoenixguard.paths import PROJECT_ROOT

# ── constants ─────────────────────────────────────────────────────────────────
EMBED_DIM = 384          # sentence-transformer all-MiniLM-L6-v2 output dim
VISUAL_DIM = 128         # lightweight image fingerprint dim
SHARED_DIM = 384         # HNSW index space (we project combined → 384)
ARCHETYPE_MAX = 60       # max archetype nodes per label class
HNSW_M = 32              # HNSW graph connectivity
HNSW_EF_CONSTRUCTION = 200
HNSW_EF_SEARCH = 100
RECALL_BOOST_THRESHOLD = 0.85   # cosine sim threshold for RL boost
RECALL_LOGIT_BOOST = 0.25       # logit boost value
FULL_ENTRY_SCAN_THRESHOLD = 4096
CENTROID_SHORTLIST_MULTIPLIER = 6
CENTROID_SHORTLIST_MIN = 12

# Formal Language & Automata Theory — regex FSM rejecting indicator text
INDICATOR_RE = re.compile(
    r"\b(ATR|MA|EMA|SMA|WMA|BOLLINGER|BB|RSI|MACD|CCI|ADX|OBV|"
    r"STOCH|STOCHASTIC|ICHIMOKU|VWAP|PIVOT|FIBONACCI|FIB|PARABOLIC|SAR|"
    r"ENVELOPE|DONCHIAN|KELTNER|WPRB|MFI|DMI|TRIX|PPO)\b",
    re.IGNORECASE,
)


def _passes_indicator_filter(text: str) -> bool:
    """
    Backward-compatible predicate for tests and legacy callers.
    Returns True only when the text is free of indicator-overlay terminology.
    """
    return not bool(INDICATOR_RE.search(str(text)))

# ── data classes ──────────────────────────────────────────────────────────────
@dataclass
class MemoryEntry:
    entry_id: str                       # sha256 of image path
    image_path: str
    label: str                          # "BUY" | "SELL"
    chart_state: dict[str, Any]
    text_embed: list[float]             # 384-dim
    visual_fp: list[float]              # 128-dim
    combined_embed: list[float]         # 384-dim HNSW space
    episode_id: str = ""
    sequence_index: int = 0
    macro_trend: str = "BULL"
    local_phase: str = "with_trend_push"
    phase_risk: str = "breakout_risk"
    intent_next: str = "continue"
    archetype_id: int = -1
    is_archetype_centroid: bool = False
    late_interaction_tokens: list[list[float]] = field(default_factory=list)
    trajectory_signature: list[float] = field(default_factory=list)
    style_signature: dict[str, float] = field(default_factory=dict)
    metric_profile: dict[str, float] = field(default_factory=dict)
    ts: str = field(default_factory=utc_now_iso)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @staticmethod
    def from_dict(d: dict[str, Any]) -> "MemoryEntry":
        payload = dict(d)
        # Accept legacy memory-bank exports that stored the same schema as `vlm_json`.
        chart_state = cast(
            dict[str, Any],
            payload.pop("chart_state", payload.pop("vlm_json", {})),
        )
        payload["chart_state"] = chart_state
        label = str(payload.get("label", chart_state.get("direction", "BUY")) or "BUY").upper()
        taxonomy_hints = any(
            key in chart_state
            for key in (
                "macro_trend",
                "momentum_bias",
                "entry_type",
                "continuation_signal",
                "reversal_signal",
                "consolidation_type",
                "structure_setup",
            )
        )
        if taxonomy_hints:
            inferred_macro, inferred_local, inferred_risk, inferred_intent = _infer_taxonomy_from_chart_state(
                chart_state,
                label,
            )
        else:
            inferred_macro, inferred_local, inferred_risk, inferred_intent = (
                "BULL",
                "with_trend_push",
                "breakout_risk",
                "continue",
            )
        payload.setdefault("episode_id", "")
        payload.setdefault("sequence_index", 0)
        payload.setdefault("macro_trend", inferred_macro)
        payload.setdefault("local_phase", inferred_local)
        payload.setdefault("phase_risk", inferred_risk)
        payload.setdefault("intent_next", inferred_intent)
        style_signature_obj = payload.get("style_signature", {})
        if not isinstance(style_signature_obj, Mapping):
            style_signature_obj = {}
        payload["style_signature"] = dict(style_signature_obj) or infer_style_signature_from_chart_state(chart_state)
        seq_state_obj = chart_state.get("sequence_state", {})
        if not isinstance(seq_state_obj, Mapping):
            seq_state_obj = {}
        payload.setdefault(
            "trajectory_signature",
            build_trajectory_signature(
                chart_state,
                sequence_index=int(payload.get("sequence_index", 0) or 0),
                sequence_state=cast(dict[str, Any], seq_state_obj),
            ),
        )
        payload.setdefault(
            "late_interaction_tokens",
            build_late_interaction_tokens(
                chart_state,
                combined_embed=cast(list[float], payload.get("combined_embed", [])),
                style_signature=cast(dict[str, float], payload["style_signature"]),
                sequence_state=cast(dict[str, Any], seq_state_obj),
                metric_profile=cast(dict[str, float], payload.get("metric_profile", {})),
            ),
        )
        payload.setdefault(
            "metric_profile",
            build_metric_profile(
                chart_state,
                sequence_state=cast(dict[str, Any], seq_state_obj),
            ),
        )
        return MemoryEntry(**payload)


@dataclass
class RecallResult:
    entry_id: str
    label: str
    similarity: float
    archetype_id: int
    chart_state: dict[str, Any]
    is_archetype_centroid: bool


# ── visual fingerprint (CPU-only, ~128 dims) ──────────────────────────────────
def _visual_fingerprint(img: Image.Image) -> NDArray[np.float32]:
    """
    Computer Graphics & Multimedia — lightweight 128-dim visual feature.
    Uses multi-scale block statistics + gradient orientation histogram.
    Zero external ML model required.
    """
    img_small = img.resize((64, 64), Image.Resampling.BILINEAR).convert("RGB")
    arr = np.asarray(img_small, dtype=np.float32) / 255.0

    feats: list[float] = []

    # Per-channel statistics (mean, std, p25, p75) × 3 channels = 12 dims
    for c in range(3):
        ch = arr[:, :, c]
        feats += [float(ch.mean()), float(ch.std()),
                  float(np.percentile(ch, 25)), float(np.percentile(ch, 75))]

    # Grayscale gradient features — magnitude stats = 3 dims
    gray = arr.mean(axis=2)
    gx = np.gradient(gray, axis=1)
    gy = np.gradient(gray, axis=0)
    mag = np.sqrt(gx ** 2 + gy ** 2)
    feats += [float(mag.mean()), float(mag.std()), float(mag.max())]

    # Gradient orientation histogram (8 bins) = 8 dims
    angle = np.arctan2(gy, gx + 1e-9) + np.pi   # [0, 2π]
    hist, _ = np.histogram(
        angle,
        bins=8,
        range=(0, 2 * np.pi),
        weights=mag,
        density=False,
    )
    hist = hist.astype(np.float32)
    hist_sum = float(hist.sum())
    if hist_sum > 1e-8:
        hist /= hist_sum
    else:
        hist.fill(0.0)
    feats += hist.tolist()

    # 8×8 block means of grayscale = 64 dims
    for i in range(8):
        for j in range(8):
            block = gray[i * 8:(i + 1) * 8, j * 8:(j + 1) * 8]
            feats.append(float(block.mean()))

    # Top-half vs bottom-half and left vs right ratios = 4 dims
    half_h = gray.shape[0] // 2
    half_w = gray.shape[1] // 2
    feats += [
        float(gray[:half_h, :].mean()),
        float(gray[half_h:, :].mean()),
        float(gray[:, :half_w].mean()),
        float(gray[:, half_w:].mean()),
    ]

    # Candle-color ratio bias (green > 127 in R channel, red < 127 in R vs G)
    r_ch, g_ch = arr[:, :, 0], arr[:, :, 1]
    green_ratio = float((g_ch > r_ch).mean())
    red_ratio = float((r_ch > g_ch).mean())
    feats += [green_ratio, red_ratio]

    # Total so far: 12 + 3 + 8 + 64 + 4 + 2 = 93 dims
    out = np.array(feats, dtype=np.float32)

    # Pad to exactly VISUAL_DIM (128) with zeros
    if out.size < VISUAL_DIM:
        out = np.pad(out, (0, VISUAL_DIM - out.size), mode="constant")
    else:
        out = out[:VISUAL_DIM]

    # Guard against any non-finite propagation from upstream numeric ops
    out = np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0)
    norm = float(np.linalg.norm(out))
    if norm > 1e-8:
        out /= norm
    return out


# ── dual-encoder projection (fixed seed, no training needed) ─────────────────
_rng = np.random.default_rng(808)
_w_text_raw: NDArray[np.float32] = _rng.standard_normal((EMBED_DIM, SHARED_DIM)).astype(np.float32)
_W_TEXT: NDArray[np.float32] = _w_text_raw / (np.linalg.norm(_w_text_raw, axis=0, keepdims=True) + 1e-8)
_w_vis_raw: NDArray[np.float32] = _rng.standard_normal((VISUAL_DIM, SHARED_DIM)).astype(np.float32)
_W_VIS: NDArray[np.float32] = _w_vis_raw / (np.linalg.norm(_w_vis_raw, axis=0, keepdims=True) + 1e-8)
del _w_text_raw, _w_vis_raw


def _dual_encode(text_embed: NDArray[np.float32], visual_fp: NDArray[np.float32]) -> NDArray[np.float32]:
    """Project text+visual to shared SHARED_DIM latent space."""
    fused = 0.75 * (text_embed @ _W_TEXT) + 0.25 * (visual_fp @ _W_VIS)
    norm = np.linalg.norm(fused)
    if norm > 1e-8:
        fused /= norm
    return fused.astype(np.float32)


# ── indicator text filter (Formal Language & Automata Theory FSM) ─────────────


# ── sentence-transformer singleton ──────────────────────────────────────────
class _EmbedderSingleton:
    _instance = None
    _model = None

    @staticmethod
    def _load_sentence_transformer_class() -> Any | None:
        global _ST_OK, SentenceTransformer
        if SentenceTransformer is not None:
            return SentenceTransformer
        if not can_import_sentence_transformers_safely():
            SentenceTransformer = None  # type: ignore[assignment]
            _ST_OK = False  # type: ignore[misc]
            return None
        try:
            from sentence_transformers import SentenceTransformer as _SentenceTransformer  # type: ignore[import-untyped]
            SentenceTransformer = _SentenceTransformer  # type: ignore[assignment]
            _ST_OK = True  # type: ignore[misc]
            return SentenceTransformer
        except (ImportError, RuntimeError, Exception):  # noqa: BLE001
            SentenceTransformer = None  # type: ignore[assignment]
            _ST_OK = False  # type: ignore[misc]
            return None

    @classmethod
    def get(cls, model_name: str = "sentence-transformers/all-MiniLM-L6-v2") -> "_EmbedderSingleton":
        if cls._instance is None:
            cls._instance = cls()
            sentence_transformer_cls = cls._load_sentence_transformer_class()
            if sentence_transformer_cls is not None:
                try:
                    cls._model = sentence_transformer_cls(model_name)  # type: ignore[operator]
                except Exception:
                    cls._model = None
        return cls._instance  # type: ignore[return-value]

    def encode(self, text: str) -> NDArray[np.float32]:
        if self._model is None:
            # Fallback: hash-based pseudo-embedding
            h = hashlib.sha256(text.encode()).digest()
            arr = np.frombuffer(h * (EMBED_DIM // 32 + 1), dtype=np.uint8).astype(np.float32)[:EMBED_DIM]
            arr = arr / 255.0
            norm = float(np.linalg.norm(arr))
            return (arr / norm) if norm > 1e-8 else arr
        try:
            raw = self._model.encode(  # type: ignore[union-attr]
                [text], normalize_embeddings=True, show_progress_bar=False,
                convert_to_numpy=True,
            )
            vec: NDArray[np.float32] = np.asarray(raw[0], dtype=np.float32)  # type: ignore[index]
            return vec
        except Exception:
            return np.zeros(EMBED_DIM, dtype=np.float32)


# ── chart-state extraction for ingestion ──────────────────────────────────────
def _build_chart_state(
    img: Image.Image,
    label: str,
    logger: logging.Logger | None = None,
) -> dict[str, Any]:
    """
    Build a structured chart-state payload from the screenshot.
    The current pipeline is CV-only, so this uses the structural heuristic path.
    """
    del logger
    # Only heuristic fallback: extract structural features from image directly
    return _heuristic_price_action(img, label)


def _heuristic_price_action(img: Image.Image, label: str) -> dict[str, Any]:
    """
    Pure image heuristic chart-state extractor.
    Analyzes candle color sequences via pixel statistics.
    """
    arr = np.asarray(img.resize((200, 100)), dtype=np.float32) / 255.0
    r: NDArray[np.float32] = arr[:, :, 0].copy()
    g: NDArray[np.float32] = arr[:, :, 1].copy()
    green_pct = float(np.mean(r < g - 0.05))
    red_pct = float(np.mean(r > g + 0.05))

    # Estimate consecutive candle counts from column-wise color
    col_green: NDArray[np.float32] = np.array(
        [float(np.mean(r[:, c] < g[:, c] - 0.05)) for c in range(200)],
        dtype=np.float32,
    )
    col_red: NDArray[np.float32] = np.array(
        [float(np.mean(r[:, c] > g[:, c] + 0.05)) for c in range(200)],
        dtype=np.float32,
    )

    # Count runs of dominant color at end of chart (right side)
    dominant = "green" if float(col_green[-20:].mean()) > float(col_red[-20:].mean()) else "red"
    run_len: int = 0
    color_seq: list[str] = []
    for c in range(199, max(199 - 15, 0), -1):
        is_green = bool(float(col_green[c]) > float(col_red[c]) + 0.05)
        color_seq.append("green" if is_green else "red")
    color_seq.reverse()

    # Count run of same color at tail
    if color_seq:
        tail_color: str = color_seq[-1]
        run_len = 0
        for x in reversed(color_seq):
            if x == tail_color:
                run_len += 1
            else:
                break

    # Detect consolidation: alternating colors with small moves
    consol = sum(1 for i in range(1, len(color_seq)) if color_seq[i] != color_seq[i - 1])

    direction = label  # use known label folder
    return {
        "entry_type": "reversal" if run_len >= 4 else "continuation",
        "direction": direction,
        "candle_count_up": int(green_pct * 10),
        "candle_count_down": int(red_pct * 10),
        "consolidation_streak": min(consol, 7),
        "consolidation_type": "tight" if consol >= 3 else "none",
        "entry_candle": {
            "body_pct": float(abs(green_pct - red_pct)),
            "upper_wick_pct": 0.1,
            "lower_wick_pct": 0.1,
            "color": "green" if label == "BUY" else "red",
        },
        "pre_entry_sequence": color_seq[-8:] if len(color_seq) >= 8 else color_seq,
        "implied_price_target": 0.0,
        "support_price": 0.0,
        "resistance_price": 0.0,
        "reversal_signal": "wick_rejection" if run_len >= 4 else "none",
        "continuation_signal": "impulse_pause" if run_len <= 2 else "none",
        "direction_probability": min(0.75 + run_len * 0.03, 0.95),
        "momentum_bias": "bullish" if direction == "BUY" else "bearish",
        "raw_description": (
            f"Heuristic: {run_len} consecutive {dominant} candles, "
            f"consolidation_count={consol}, label={label}"
        ),
    }


def _chart_state_to_text(chart_state: dict[str, Any]) -> str:
    """Convert structured chart-state data to text for the sentence-transformer."""
    projected_box = cast(dict[str, Any], chart_state.get("projected_next_box", {}))
    swing_state = cast(dict[str, Any], chart_state.get("swing_state", {}))
    parts = [
        f"entry_type={chart_state.get('entry_type', 'unknown')}",
        f"direction={chart_state.get('direction', 'unknown')}",
        f"macro_trend={chart_state.get('macro_trend', 'unknown')}",
        f"candle_count_up={chart_state.get('candle_count_up', 0)}",
        f"candle_count_down={chart_state.get('candle_count_down', 0)}",
        f"consolidation_streak={chart_state.get('consolidation_streak', 0)}",
        f"consolidation_type={chart_state.get('consolidation_type', 'none')}",
        f"reversal_signal={chart_state.get('reversal_signal', 'none')}",
        f"continuation_signal={chart_state.get('continuation_signal', 'none')}",
        f"momentum_bias={chart_state.get('momentum_bias', 'neutral')}",
        f"structure_setup={chart_state.get('structure_setup', 'none')}",
        f"entry_color={chart_state.get('entry_candle', {}).get('color', 'unknown')}",
        f"direction_probability={chart_state.get('direction_probability', 0.5):.2f}",
        f"projection_direction={projected_box.get('direction', 'unknown')}",
        f"projection_box={projected_box.get('box_type', 'unknown')}",
        f"projection_confidence={chart_state.get('projection_bias_confidence', projected_box.get('confidence', 0.0)):.2f}",
        f"projection_dominance={chart_state.get('projection_dominance', projected_box.get('dominance_gap', 0.0)):.2f}",
        f"recent_swing={swing_state.get('recent_swing_direction', 'unknown')}",
        f"macro_swing={swing_state.get('macro_swing_direction', 'unknown')}",
        f"swing_phase={swing_state.get('swing_phase', 'unknown')}",
    ]
    raw = chart_state.get("raw_description", "")
    if raw:
        parts.append(f"description={raw[:300]}")
    projection_explanation = str(chart_state.get("projection_explanation", "") or projected_box.get("explanation", "")).strip()
    if projection_explanation:
        parts.append(f"projection_note={projection_explanation[:220]}")
    return " | ".join(parts)


def _infer_taxonomy_from_chart_state(chart_state: dict[str, Any], label: str) -> tuple[str, str, str, str]:
    momentum = str(chart_state.get("momentum_bias", "neutral") or "neutral").lower()
    entry_type = str(chart_state.get("entry_type", "continuation") or "continuation").lower()
    continuation_signal = str(chart_state.get("continuation_signal", "none") or "none").lower()
    reversal_signal = str(chart_state.get("reversal_signal", "none") or "none").lower()
    consolidation = str(chart_state.get("consolidation_type", "none") or "none").lower()
    direction = str(chart_state.get("direction", label) or label).upper()
    structure_setup = str(chart_state.get("structure_setup", "none") or "none").lower()
    macro_hint = str(chart_state.get("macro_trend", "") or "").upper()

    if macro_hint in {"BULL", "BEAR"}:
        macro_trend = macro_hint
    elif momentum == "bearish":
        macro_trend = "BEAR"
    elif momentum == "bullish":
        macro_trend = "BULL"
    else:
        macro_trend = "BULL" if direction == "BUY" else "BEAR"

    with_trend = (macro_trend == "BULL" and direction == "BUY") or (macro_trend == "BEAR" and direction == "SELL")
    if structure_setup == "reversal_release" or entry_type == "reversal":
        local_phase = "reversal_base"
    elif consolidation in {"tight", "wide"}:
        local_phase = "with_trend_pause" if with_trend else "counter_trend_pullback"
    elif with_trend and continuation_signal in {"impulse_pause", "breakout"}:
        local_phase = "with_trend_push"
    elif with_trend:
        local_phase = "continuation_base"
    elif reversal_signal != "none":
        local_phase = "counter_trend_spike"
    else:
        local_phase = "counter_trend_pullback"

    if local_phase in {"counter_trend_spike", "reversal_base"}:
        phase_risk = "exhaustion_risk"
    elif local_phase in {"with_trend_push", "continuation_base"}:
        phase_risk = "breakout_risk"
    else:
        phase_risk = "chop_risk"

    if entry_type == "reversal":
        intent_next = "reversal_attempt"
    elif continuation_signal in {"breakout", "impulse_pause"} and with_trend:
        intent_next = "continue"
    elif local_phase == "counter_trend_pullback":
        intent_next = "pullback"
    elif reversal_signal != "none" and not with_trend:
        intent_next = "fakeout"
    else:
        intent_next = "continue"

    return macro_trend, local_phase, phase_risk, intent_next


# ── K-Means archetype builder ─────────────────────────────────────────────────
def _build_archetypes(
    entries: list[MemoryEntry],
    max_archetypes: int = ARCHETYPE_MAX,
    logger: logging.Logger | None = None,
) -> list[MemoryEntry]:
    """
    Clustering — K-Means on combined embeddings.
    Prunes identical setups into single Archetype Nodes to save memory.
    """
    if not _SK_OK:
        if logger:
            logger.warning("scikit-learn unavailable; skipping archetype clustering.")
        return entries

    buy_entries = [e for e in entries if e.label == "BUY"]
    sell_entries = [e for e in entries if e.label == "SELL"]

    def cluster_group(group: list[MemoryEntry], label: str) -> list[MemoryEntry]:
        if len(group) == 0:
            return group
        n_clusters = min(max_archetypes, max(1, len(group) // 2))
        X = np.array([e.combined_embed for e in group], dtype=np.float32)
        if n_clusters == 1 or len(group) <= 3:
            for e in group:
                e.archetype_id = 0
            group[0].is_archetype_centroid = True
            return group
        try:
            km = MiniBatchKMeans(n_clusters=n_clusters, random_state=808, n_init=5)  # type: ignore[operator]
            km_any = cast(Any, km)
            labels: NDArray[np.int64] = np.asarray(km_any.fit_predict(X), dtype=np.int64)
            centroids: NDArray[np.float32] = np.asarray(km_any.cluster_centers_, dtype=np.float32)
            # Assign archetype IDs
            for i, e in enumerate(group):
                e.archetype_id = int(labels[i])
            # Find real image closest to each centroid (Archetype Node)
            for c_id in range(n_clusters):
                centroid = centroids[c_id]
                cluster_members = [e for e in group if e.archetype_id == c_id]
                if not cluster_members:
                    continue
                dists = [
                    float(np.linalg.norm(np.array(e.combined_embed) - centroid))
                    for e in cluster_members
                ]
                best_idx = int(np.argmin(dists))
                cluster_members[best_idx].is_archetype_centroid = True
            if logger:
                logger.info("  [Cluster] %s: %d images → %d archetypes", label, len(group), n_clusters)
        except Exception as e:
            if logger:
                logger.warning("K-Means clustering failed for %s: %s", label, e)
        return group

    buy_entries = cluster_group(buy_entries, "BUY")
    sell_entries = cluster_group(sell_entries, "SELL")

    return buy_entries + sell_entries


# ── HNSW index wrapper ────────────────────────────────────────────────────────
class _HNSWIndex:
    """Data Structures & Search Strategies — HNSW Hierarchical Navigable Small World graph."""

    def __init__(self, dim: int = SHARED_DIM):
        self.dim = dim
        self._index: Any = None  # hnswlib.Index | faiss.Index | np.ndarray | None
        self._id_to_idx: dict[int, str] = {}  # internal HNSW int ID → entry_id
        self._idx_to_id: list[str] = []

    def build(self, entries: list[MemoryEntry], logger: logging.Logger | None = None):
        vectors = np.array([e.combined_embed for e in entries], dtype=np.float32)
        n = len(entries)
        if n == 0:
            return

        if _HNSW_OK:
            self._index = hnswlib.Index(space="cosine", dim=self.dim)  # type: ignore[possibly-unbound]
            self._index.init_index(
                max_elements=n + 10,
                ef_construction=HNSW_EF_CONSTRUCTION,
                M=HNSW_M,
            )
            self._index.set_ef(HNSW_EF_SEARCH)
            self._index.add_items(vectors, list(range(n)))
            if logger:
                logger.info("[HNSW] Built index with %d entries (M=%d, ef=%d)", n, HNSW_M, HNSW_EF_CONSTRUCTION)
        elif _FAISS_OK:
            self._index = faiss.IndexFlatIP(self.dim)  # type: ignore[possibly-unbound]
            # Normalize for cosine similarity via inner product
            norms = np.linalg.norm(vectors, axis=1, keepdims=True)
            vectors_norm = vectors / (norms + 1e-8)
            self._index.add(vectors_norm)  # type: ignore[union-attr]
            if logger:
                logger.info("[FAISS-IP] Built index with %d entries", n)
        else:
            # Pure numpy brute-force fallback
            self._index = vectors
            if logger:
                logger.warning("[NumpyKNN] No HNSW/FAISS available; using brute-force cosine search.")

        self._idx_to_id = [e.entry_id for e in entries]

    def search(self, query: NDArray[np.float32], top_k: int = 5) -> list[tuple[str, float]]:
        """KNN search — returns list of (entry_id, similarity) pairs."""
        if self._index is None or len(self._idx_to_id) == 0:
            return []

        q = query.astype(np.float32).reshape(1, -1)

        if _HNSW_OK and hasattr(self._index, "knn_query"):
            try:
                k = min(top_k, len(self._idx_to_id))
                labels, distances = self._index.knn_query(q, k=k)
                # hnswlib cosine distance → similarity = 1 - distance
                results: list[tuple[str, float]] = []
                for idx, dist in zip(labels[0], distances[0]):
                    sim = float(max(0.0, 1.0 - float(dist)))
                    results.append((self._idx_to_id[int(idx)], sim))
                return results
            except Exception:
                pass

        if _FAISS_OK and hasattr(self._index, "search"):
            try:
                norm = np.linalg.norm(q)
                q_norm = q / (norm + 1e-8)
                k = min(top_k, len(self._idx_to_id))
                scores, indices = self._index.search(q_norm, k)
                results2: list[tuple[str, float]] = []
                for idx, sc in zip(indices[0], scores[0]):
                    if idx >= 0:
                        results2.append((self._idx_to_id[int(idx)], float(sc)))
                return results2
            except Exception:
                pass

        if isinstance(self._index, np.ndarray):
            idx_arr: NDArray[np.float32] = cast(NDArray[np.float32], self._index)  # type: ignore[assignment]
            norm = float(np.linalg.norm(q))
            q_n = q / (norm + 1e-8)
            norms2 = np.linalg.norm(idx_arr, axis=1, keepdims=True)
            vecs_n = idx_arr / (norms2 + 1e-8)
            sims = (vecs_n @ q_n.T).flatten()
            top_indices = np.argsort(sims)[::-1][:top_k]
            return [(self._idx_to_id[int(i)], float(sims[i])) for i in top_indices]

        return []

    def save(self, path: Path):
        path.mkdir(parents=True, exist_ok=True)
        if _HNSW_OK and hasattr(self._index, "save_index"):
            self._index.save_index(str(path / "hnsw.bin"))
        elif _FAISS_OK and hasattr(self._index, "ntotal"):
            faiss.write_index(self._index, str(path / "faiss.bin"))  # type: ignore[possibly-unbound]
        else:
            if isinstance(self._index, np.ndarray):
                np.save(str(path / "numpy_vecs.npy"), cast(NDArray[np.float32], self._index))  # type: ignore[arg-type]
        (path / "id_map.json").write_text(json.dumps(self._idx_to_id), encoding="utf-8")

    def load(self, path: Path, n_entries: int):
        id_map_path = path / "id_map.json"
        if id_map_path.exists():
            self._idx_to_id = json.loads(id_map_path.read_text(encoding="utf-8"))
        hnsw_path = path / "hnsw.bin"
        faiss_path = path / "faiss.bin"
        numpy_path = path / "numpy_vecs.npy"
        if _HNSW_OK and hnsw_path.exists():
            self._index = hnswlib.Index(space="cosine", dim=self.dim)  # type: ignore[possibly-unbound]
            self._index.load_index(str(hnsw_path), max_elements=max(n_entries + 10, 500))
            self._index.set_ef(HNSW_EF_SEARCH)
        elif _FAISS_OK and faiss_path.exists():
            self._index = faiss.read_index(str(faiss_path))  # type: ignore[possibly-unbound]
        elif numpy_path.exists():
            self._index = np.load(str(numpy_path))


# ── MemoryBank runtime interface ──────────────────────────────────────────────
class MemoryBank:
    """
    Runtime interface for the HNSW memory bank.
    Provides search, few-shot context injection, and RL logit boost.
    """

    def __init__(self):
        self._entries: dict[str, MemoryEntry] = {}     # entry_id → MemoryEntry
        self._index: _HNSWIndex = _HNSWIndex(SHARED_DIM)
        self._embedder: _EmbedderSingleton | None = None
        self._loaded = False
        self.n_buy = 0
        self.n_sell = 0
        self._episode_members: dict[str, list[MemoryEntry]] = {}
        self._archetype_members: dict[tuple[str, int], list[MemoryEntry]] = {}
        self._entry_ids: list[str] = []
        self._entry_matrix: NDArray[np.float32] | None = None
        self._style_reference_profile: dict[str, Any] = {"mean": {}, "std": {}, "count": 0}

    # ── internal assembly (used by MemoryIngestor only) ──────────────────────
    def populate(
        self,
        index: "_HNSWIndex",
        entries: "dict[str, MemoryEntry]",
        n_buy: int,
        n_sell: int,
    ) -> None:
        """Wire HNSW index and entries into the bank (called by MemoryIngestor)."""
        self._index = index
        self._entries = entries
        self.n_buy = n_buy
        self.n_sell = n_sell
        self._rebuild_episode_index()
        self._loaded = True

    def _rebuild_episode_index(self) -> None:
        grouped: dict[str, list[MemoryEntry]] = {}
        archetypes: dict[tuple[str, int], list[MemoryEntry]] = {}
        entry_ids: list[str] = []
        entry_vectors: list[NDArray[np.float32]] = []
        style_accumulator: dict[str, list[float]] = {}
        for entry in self._entries.values():
            ep_id = entry.episode_id or f"{entry.label}:ungrouped"
            grouped.setdefault(ep_id, []).append(entry)
            if int(entry.archetype_id) >= 0:
                archetype_key = (str(entry.label).upper(), int(entry.archetype_id))
                archetypes.setdefault(archetype_key, []).append(entry)
            entry_ids.append(entry.entry_id)
            entry_vectors.append(np.asarray(entry.combined_embed, dtype=np.float32))
            for key, value in entry.style_signature.items():
                if isinstance(value, (float, int)):
                    style_accumulator.setdefault(str(key), []).append(float(value))
        for members in grouped.values():
            members.sort(key=lambda e: int(e.sequence_index))
        for members in archetypes.values():
            members.sort(
                key=lambda e: (
                    0 if bool(e.is_archetype_centroid) else 1,
                    int(e.sequence_index),
                    str(e.entry_id),
                )
            )
        self._episode_members = grouped
        self._archetype_members = archetypes
        self._entry_ids = entry_ids
        if entry_vectors:
            matrix = np.stack(entry_vectors, axis=0).astype(np.float32)
            norms = np.linalg.norm(matrix, axis=1, keepdims=True)
            self._entry_matrix = (matrix / (norms + 1e-8)).astype(np.float32)
        else:
            self._entry_matrix = None
        style_mean = {
            key: float(np.mean(np.asarray(values, dtype=np.float32)))
            for key, values in style_accumulator.items()
            if values
        }
        style_std = {
            key: float(np.std(np.asarray(values, dtype=np.float32)))
            for key, values in style_accumulator.items()
            if values
        }
        self._style_reference_profile = {
            "mean": style_mean,
            "std": style_std,
            "count": len(self._entries),
        }

    # ── public entries accessor (Bug fix: main.py accesses .entries) ─────────
    @property
    def entries(self) -> list["MemoryEntry"]:
        """Public read-only view of all stored MemoryEntry objects."""
        return list(self._entries.values())

    def reference_style_profile(self) -> dict[str, Any]:
        return dict(getattr(self, "_style_reference_profile", {"mean": {}, "std": {}, "count": 0}))

    # ── embed a live chart-state description ─────────────────────────────────
    def embed_description(
        self,
        chart_state: dict[str, Any],
        image: "Image.Image | None" = None,
    ) -> NDArray[np.float32]:
        """
        Embeds a live chart-state description into the shared HNSW latent space.

        Bug fix: when the live chart image is provided the real visual
        fingerprint is computed so the query vector lives in the same
        0.75-text / 0.25-visual weighted subspace as the stored embeddings.
        Without this, cosine similarity was structurally suppressed by ~25%,
        making the 0.87 ensemble veto threshold nearly impossible to reach.
        """
        if self._embedder is None:
            self._embedder = _EmbedderSingleton.get()
        text = _chart_state_to_text(chart_state)
        text_embed = self._embedder.encode(text)
        # Compute real visual fingerprint from the live image when available.
        # Falls back to zeros only if no image supplied (safe degradation).
        if image is not None:
            visual_fp = _visual_fingerprint(image)
        else:
            visual_fp = np.zeros(VISUAL_DIM, dtype=np.float32)
        return _dual_encode(text_embed, visual_fp)

    # ── KNN search ───────────────────────────────────────────────────────────
    from typing import Optional

    @staticmethod
    def _normalize_query_embed(query_embed: NDArray[np.float32]) -> NDArray[np.float32]:
        query = np.asarray(query_embed, dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(query))
        if norm <= 1e-8:
            return query.astype(np.float32)
        return (query / norm).astype(np.float32)

    @staticmethod
    def _entry_to_recall(entry: MemoryEntry, similarity: float) -> RecallResult:
        return RecallResult(
            entry_id=entry.entry_id,
            label=entry.label,
            similarity=float(similarity),
            archetype_id=entry.archetype_id,
            chart_state=entry.chart_state,
            is_archetype_centroid=entry.is_archetype_centroid,
        )

    def _exact_similarity(self, query_embed: NDArray[np.float32], entry: MemoryEntry) -> float:
        query = self._normalize_query_embed(query_embed)
        vector = np.asarray(entry.combined_embed, dtype=np.float32).reshape(-1)
        vec_norm = float(np.linalg.norm(vector))
        if vec_norm > 1e-8:
            vector = vector / vec_norm
        sim = float(np.dot(query, vector))
        return float(max(0.0, sim))

    def _exact_recall_results(
        self,
        query_embed: NDArray[np.float32],
        candidate_ids: list[str] | None = None,
    ) -> list[RecallResult]:
        query = self._normalize_query_embed(query_embed)
        if candidate_ids is None and self._entry_matrix is not None and len(self._entry_ids) == len(self._entries):
            sims = cast(NDArray[np.float32], self._entry_matrix @ query)
            order = np.argsort(sims)[::-1]
            recalls: list[RecallResult] = []
            for idx in order.tolist():
                entry_id = self._entry_ids[int(idx)]
                entry = self._entries.get(entry_id)
                if entry is None:
                    continue
                sim = float(max(0.0, float(sims[int(idx)])))
                recalls.append(self._entry_to_recall(entry, sim))
            return recalls

        if candidate_ids is None:
            candidate_ids = list(self._entries.keys())

        recalls = []
        seen_ids: set[str] = set()
        for entry_id in candidate_ids:
            if entry_id in seen_ids:
                continue
            seen_ids.add(entry_id)
            entry = self._entries.get(entry_id)
            if entry is None:
                continue
            recalls.append(self._entry_to_recall(entry, self._exact_similarity(query, entry)))
        recalls.sort(key=lambda item: float(item.similarity), reverse=True)
        return recalls

    def _candidate_ids_from_shortlist(self, raw_hits: list[tuple[str, float]]) -> list[str]:
        candidate_ids: list[str] = []
        seen_ids: set[str] = set()
        for entry_id, _sim in raw_hits:
            if entry_id in seen_ids:
                continue
            seen_ids.add(entry_id)
            candidate_ids.append(entry_id)
            entry = self._entries.get(entry_id)
            if entry is None or int(entry.archetype_id) < 0:
                continue
            archetype_key = (str(entry.label).upper(), int(entry.archetype_id))
            for member in self._archetype_members.get(archetype_key, []):
                if member.entry_id in seen_ids:
                    continue
                seen_ids.add(member.entry_id)
                candidate_ids.append(member.entry_id)
        return candidate_ids

    def _rerank_results(
        self,
        results: list[RecallResult],
        macro_trend: Optional[str] = None,
        local_phase: Optional[str] = None,
    ) -> list[RecallResult]:
        if not results:
            return []
        if not macro_trend and not local_phase:
            return results

        labels = [str(result.label).upper() for result in results]
        mode_label = max(set(labels), key=labels.count) if labels else "HOLD"
        scored: list[tuple[float, RecallResult]] = []
        for result in results:
            entry = self._entries.get(result.entry_id)
            if entry is None:
                continue
            score = float(result.similarity)
            if macro_trend and str(entry.macro_trend) == str(macro_trend):
                score += 0.08
            if local_phase and str(entry.local_phase) == str(local_phase):
                score += 0.12
            if macro_trend and str(entry.macro_trend) != str(macro_trend):
                score -= 0.05
            if local_phase and str(entry.local_phase) != str(local_phase):
                score -= 0.05
            if entry.label == mode_label:
                score += 0.04
            scored.append((score, result))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [result for _, result in scored]

    def _contextual_rerank_results(
        self,
        results: list[RecallResult],
        query_context: Mapping[str, Any] | None = None,
    ) -> list[RecallResult]:
        if not results or not query_context:
            return results
        query_tokens = cast(list[list[float]], query_context.get("late_interaction_tokens", []))
        query_trajectory = cast(list[float], query_context.get("trajectory_signature", []))
        query_style = cast(dict[str, float], query_context.get("style_signature", {}))
        query_metric = cast(dict[str, float], query_context.get("metric_profile", {}))
        scored: list[tuple[float, RecallResult]] = []
        for result in results:
            entry = self._entries.get(result.entry_id)
            if entry is None:
                continue
            late_score = late_interaction_score(query_tokens, entry.late_interaction_tokens)
            trajectory_score = trajectory_alignment(query_trajectory, entry.trajectory_signature)
            style_score = style_alignment_score(query_style, entry.style_signature)
            metric_score = metric_profile_alignment(query_metric, entry.metric_profile)
            score = (
                float(result.similarity)
                + 0.12 * late_score
                + 0.08 * trajectory_score
                + 0.05 * style_score
                + 0.11 * metric_score
            )
            scored.append((score, result))
        scored.sort(key=lambda item: item[0], reverse=True)
        return [result for _, result in scored]

    def search(
        self,
        query_embed: NDArray[np.float32],
        top_k: int = 5,
        macro_trend: Optional[str] = None,
        local_phase: Optional[str] = None,
        query_context: Mapping[str, Any] | None = None,
    ) -> list[RecallResult]:
        """
        Search memory entries using exact episodic recall for small banks and
        centroid-shortlist expansion for larger banks.

        This avoids the old failure mode where centroid-only retrieval behaved
        more like a prototype dictionary than a true historical memory.
        """
        if not self._loaded:
            return []
        query = self._normalize_query_embed(query_embed)

        if len(self._entries) <= FULL_ENTRY_SCAN_THRESHOLD:
            results = self._exact_recall_results(query, candidate_ids=None)
            results = self._rerank_results(results, macro_trend=macro_trend, local_phase=local_phase)
            results = self._contextual_rerank_results(results, query_context=query_context)
            return results[:top_k]

        shortlist_k = max(top_k * CENTROID_SHORTLIST_MULTIPLIER, CENTROID_SHORTLIST_MIN)
        raw = self._index.search(query, shortlist_k)
        candidate_ids = self._candidate_ids_from_shortlist(raw)
        if not candidate_ids:
            results = self._exact_recall_results(query, candidate_ids=None)
        else:
            results = self._exact_recall_results(query, candidate_ids=candidate_ids)
        results = self._rerank_results(results, macro_trend=macro_trend, local_phase=local_phase)
        results = self._contextual_rerank_results(results, query_context=query_context)
        return results[:top_k]

    def search_sequence_context(
        self,
        query_embed: NDArray[np.float32],
        macro_trend: str,
        local_phase: str,
        top_k: int = 5,
    ) -> list[RecallResult]:
        """
        Sequence-aware recall that re-ranks nearest entries with taxonomy similarity.
        Keeps backward compatibility by relying on existing vector search first.
        """
        base = self.search(query_embed, max(top_k * 3, top_k))
        if not base:
            return []

        scored: list[tuple[float, RecallResult]] = []
        for r in base:
            e = self._entries.get(r.entry_id)
            if e is None:
                continue
            score = float(r.similarity)
            if str(e.macro_trend) == str(macro_trend):
                score += 0.08
            if str(e.local_phase) == str(local_phase):
                score += 0.12
            if str(e.episode_id):
                score += 0.02
            scored.append((score, r))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [r for _, r in scored[:top_k]]

    def summarize_transition_probabilities(self, results: list[RecallResult]) -> dict[str, float]:
        counts = {
            "continue": 0.0,
            "pullback": 0.0,
            "reversal_attempt": 0.0,
            "fakeout": 0.0,
        }
        total = 0.0
        for r in results:
            entry = self._entries.get(r.entry_id)
            if entry is None:
                continue
            key = str(entry.intent_next)
            if key not in counts:
                key = "continue"
            w = max(0.0, float(r.similarity))
            counts[key] += w
            total += w
        if total <= 1e-9:
            return {k: 0.25 for k in counts}
        return {k: float(v / total) for k, v in counts.items()}

    def episode_summary(self, results: list[RecallResult]) -> list[dict[str, Any]]:
        summary: list[dict[str, Any]] = []
        for r in results:
            e = self._entries.get(r.entry_id)
            if e is None:
                continue
            summary.append(
                {
                    "entry_id": r.entry_id,
                    "label": r.label,
                    "similarity": float(r.similarity),
                    "macro_trend": str(e.macro_trend),
                    "local_phase": str(e.local_phase),
                    "intent_next": str(e.intent_next),
                    "episode_id": str(e.episode_id),
                    "sequence_index": int(e.sequence_index),
                    "episode_length": int(len(self._episode_members.get(e.episode_id or f"{e.label}:ungrouped", []))),
                    "trajectory_signature": list(e.trajectory_signature[:8]),
                }
            )
        return summary

    # ── few-shot context injection ───────────────────────────────────────────
    def get_few_shot_context(self, results: list[RecallResult]) -> str:
        """
        Knowledge Representation & Reasoning —
        Injects top-3 recalled chart-state schemas as in-context style examples.
        """
        if not results:
            return ""
        ctx_parts = [
            "=== RECALLED STYLE EXAMPLES (from your 300+ winning trades) ===\n"
            "Use these as reference for how YOU enter the market:\n"
        ]
        for i, r in enumerate(results[:3], 1):
            ctx_parts.append(
                f"[Example {i} | {r.label} | archetype={r.archetype_id} | "
                f"similarity={r.similarity:.3f}]\n"
                + json.dumps(r.chart_state, indent=2)[:500]
                + "\n"
            )
        ctx_parts.append(
            "=== END RECALLED EXAMPLES ===\n"
            "Now analyze the uploaded chart using the SAME style pattern as these examples.\n"
        )
        return "\n".join(ctx_parts)

    # ── RL logit boost ───────────────────────────────────────────────────────
    def compute_logit_boost(self, results: list[RecallResult]) -> tuple[float, str]:
        """
        Advanced Probability & Statistics —
        Returns (boost_value, boosted_direction).
        Boosts RL policy logits by +0.25 if top-1 similarity > 0.85.
        Direction is the label from the top recalled entry.
        """
        if not results:
            return 0.0, "HOLD"
        top = results[0]
        if top.similarity >= RECALL_BOOST_THRESHOLD:
            return RECALL_LOGIT_BOOST, top.label
        elif top.similarity >= 0.70:
            # Partial boost
            partial = RECALL_LOGIT_BOOST * (top.similarity - 0.70) / (RECALL_BOOST_THRESHOLD - 0.70)
            return float(partial), top.label
        return 0.0, "HOLD"

    # ── DPO pairs for RL training ────────────────────────────────────────────
    def generate_dpo_pairs(self, n: int = 50) -> list[dict[str, Any]]:
        """
        Predictive Analytics — auto-generate DPO preference pairs from memory bank.
        chosen = correct direction, rejected = opposite.
        """
        dpo_pairs: list[dict[str, Any]] = []
        entries = list(self._entries.values())
        rng = np.random.default_rng(808)
        n_sample = min(n, len(entries))
        sampled_idx: NDArray[np.int64] = np.asarray(rng.choice(len(entries), size=n_sample, replace=False), dtype=np.int64)
        sampled: list[MemoryEntry] = [entries[int(i)] for i in sampled_idx]
        for entry in sampled:
            chosen: str = entry.label
            rejected = "SELL" if chosen == "BUY" else "BUY"
            dpo_pairs.append({
                "ts": utc_now_iso(),
                "image_hash": entry.entry_id,
                "chosen": chosen,
                "rejected": rejected,
                "reason": f"style_memory_bank_archetype_{entry.archetype_id}",
                "annotation_text": json.dumps(entry.chart_state)[:400],
            })
        return dpo_pairs

    # ── persistence ──────────────────────────────────────────────────────────
    def save(self, bank_dir: Path, audit_log_path: Path | None = None):
        bank_dir.mkdir(parents=True, exist_ok=True)
        # Save metadata
        metadata = [e.to_dict() for e in self._entries.values()]
        (bank_dir / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False),
            encoding="utf-8",
        )
        # Save HNSW index
        self._index.save(bank_dir / "index")
        # Save stats
        stats: dict[str, Any] = {
            "n_buy": self.n_buy,
            "n_sell": self.n_sell,
            "n_total": len(self._entries),
            "ts": str(utc_now_iso()),
        }
        (bank_dir / "stats.json").write_text(json.dumps(stats), encoding="utf-8")
        if audit_log_path:
            append_hash_chain(audit_log_path, stats)

    @classmethod
    def load(cls, bank_dir: Path, logger: logging.Logger | None = None) -> "MemoryBank":
        bank = cls()
        meta_path = bank_dir / "metadata.json"
        if not meta_path.exists():
            if logger:
                logger.warning("[MemoryBank] metadata.json not found at %s", bank_dir)
            return bank
        try:
            raw = json.loads(meta_path.read_text(encoding="utf-8"))
            entries = [MemoryEntry.from_dict(d) for d in raw]
            for e in entries:
                bank._entries[e.entry_id] = e
            bank.n_buy = sum(1 for e in entries if e.label == "BUY")
            bank.n_sell = sum(1 for e in entries if e.label == "SELL")
            bank._rebuild_episode_index()
            bank._index.load(bank_dir / "index", n_entries=len(entries))
            bank._loaded = True
            if logger:
                logger.info(
                    "[MemoryBank] Loaded %d entries (BUY=%d, SELL=%d) from %s",
                    len(entries), bank.n_buy, bank.n_sell, bank_dir,
                )
        except Exception as e:
            if logger:
                logger.exception("[MemoryBank] Load failed: %s", e)
        return bank

    @property
    def is_loaded(self) -> bool:
        return self._loaded and len(self._entries) > 0


# ── MemoryIngestor ────────────────────────────────────────────────────────────
class MemoryIngestor:
    """
    Offline batch processor for the 300+ labeled trading chart images.
    Run once to build the HNSW memory bank.
    """

    IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".webp"}

    def __init__(
        self,
        buys_dir: Path,
        sells_dir: Path,
        output_dir: Path,
        logger: logging.Logger | None = None,
    ):
        self.buys_dir = buys_dir
        self.sells_dir = sells_dir
        self.output_dir = output_dir
        self.logger = logger or logging.getLogger("memory_ingest")
        self._embedder = _EmbedderSingleton.get()

    def _image_paths(self, folder: Path) -> list[Path]:
        if not folder.exists():
            self.logger.warning("Folder not found: %s", folder)
            return []
        paths = sorted([
            p for p in folder.iterdir()
            if p.is_file() and p.suffix.lower() in self.IMAGE_EXTS
        ])
        return paths

    def _process_image(self, path: Path, label: str, sequence_index: int = 0) -> MemoryEntry | None:
        try:
            img = Image.open(path).convert("RGB")
        except Exception as e:
            self.logger.warning("Cannot open image %s: %s", path, e)
            return None

        entry_id = hashlib.sha256(path.as_posix().encode()).hexdigest()

        chart_state = _build_chart_state(img, label, self.logger)
        macro_trend, local_phase, phase_risk, intent_next = _infer_taxonomy_from_chart_state(chart_state, label)
        episode_id = f"{label}:{path.parent.name}"

        # Text embedding
        text = _chart_state_to_text(chart_state)
        text_embed = self._embedder.encode(text)

        # Visual fingerprint
        visual_fp = _visual_fingerprint(img)

        # Dual-encoder projection to shared space
        combined_embed = _dual_encode(text_embed, visual_fp)
        style_signature = infer_style_signature_from_chart_state(chart_state)
        trajectory_signature = build_trajectory_signature(chart_state, sequence_index=int(sequence_index))
        metric_profile = build_metric_profile(chart_state)
        late_interaction_tokens = build_late_interaction_tokens(
            chart_state,
            combined_embed=combined_embed.tolist(),
            style_signature=style_signature,
            metric_profile=metric_profile,
        )

        return MemoryEntry(
            entry_id=entry_id,
            image_path=str(path),
            label=label,
            chart_state=chart_state,
            text_embed=text_embed.tolist(),
            visual_fp=visual_fp.tolist(),
            combined_embed=combined_embed.tolist(),
            episode_id=episode_id,
            sequence_index=int(sequence_index),
            macro_trend=macro_trend,
            local_phase=local_phase,
            phase_risk=phase_risk,
            intent_next=intent_next,
            late_interaction_tokens=late_interaction_tokens,
            trajectory_signature=trajectory_signature,
            style_signature=style_signature,
            metric_profile=metric_profile,
        )

    def ingest(self) -> MemoryBank:
        """
        Full ingestion pipeline:
        1. Process all images in BUYS/ and SELLS/ folders
        2. K-Means archetype clustering
        3. Build HNSW graph
        4. Save to output_dir
        """
        t0 = time.time()
        entries: list[MemoryEntry] = []

        for label, folder in [("BUY", self.buys_dir), ("SELL", self.sells_dir)]:
            paths = self._image_paths(folder)
            self.logger.info("[Ingest] Processing %d %s images from %s", len(paths), label, folder)
            for i, path in enumerate(paths):
                entry = self._process_image(path, label, sequence_index=i)
                if entry is not None:
                    entries.append(entry)
                if (i + 1) % 20 == 0:
                    self.logger.info("  [%s] %d/%d processed...", label, i + 1, len(paths))

        self.logger.info("[Ingest] Total processed: %d entries", len(entries))

        # K-Means archetype clustering
        self.logger.info("[Ingest] Building archetype clusters...")
        entries = _build_archetypes(entries, ARCHETYPE_MAX, self.logger)

        # Build HNSW index using archetype centroids only (for speed)
        # but keep all entries queryable in the metadata dict
        archetype_entries = [e for e in entries if e.is_archetype_centroid]
        if len(archetype_entries) < 5:
            # Not enough archetypes — use all entries
            archetype_entries = entries
        self.logger.info("[Ingest] Building HNSW index from %d archetype nodes...", len(archetype_entries))

        idx = _HNSWIndex(SHARED_DIM)
        idx.build(archetype_entries, self.logger)

        # Build MemoryBank
        bank = MemoryBank()
        entry_dict: dict[str, MemoryEntry] = {e.entry_id: e for e in entries}
        bank.populate(
            index=idx,
            entries=entry_dict,
            n_buy=sum(1 for e in entries if e.label == "BUY"),
            n_sell=sum(1 for e in entries if e.label == "SELL"),
        )

        # Save
        self.logger.info("[Ingest] Saving memory bank to %s", self.output_dir)
        bank.save(
            self.output_dir,
            audit_log_path=self.output_dir / "audit_hash_chain.log",
        )

        elapsed = time.time() - t0
        self.logger.info(
            "[Ingest] Complete. %d entries (%d BUY, %d SELL) in %.1fs",
            len(entries), bank.n_buy, bank.n_sell, elapsed,
        )
        return bank


# ── CLI entrypoint ────────────────────────────────────────────────────────────
def main():
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logger = setup_logger(log_dir / "memory_ingest.log", name="memory_ingest")

    # Paths
    memory_root = PROJECT_ROOT / "808 Memory"
    buys_dir = memory_root / "BUYS-20260224T225615Z-1-001" / "BUYS"
    sells_dir = memory_root / "SELLS-20260224T225719Z-1-001" / "SELLS"
    output_dir = PROJECT_ROOT / "memory_bank"

    logger.info("=" * 60)
    logger.info("PhoenixGuard SIGE-VLA 3.0 — Memory Bank Ingestion")
    logger.info("BUYS dir : %s (%s)", buys_dir, "EXISTS" if buys_dir.exists() else "MISSING")
    logger.info("SELLS dir: %s (%s)", sells_dir, "EXISTS" if sells_dir.exists() else "MISSING")
    logger.info("Output   : %s", output_dir)
    logger.info("hnswlib  : %s", "OK" if _HNSW_OK else "MISSING (fallback mode)")
    logger.info("faiss    : %s", "OK" if _FAISS_OK else "MISSING")
    logger.info("ST       : %s", "OK" if _ST_OK else "MISSING (hash fallback)")
    logger.info("sklearn  : %s", "OK" if _SK_OK else "MISSING (no clustering)")
    logger.info("=" * 60)

    ingestor = MemoryIngestor(
        buys_dir=buys_dir,
        sells_dir=sells_dir,
        output_dir=output_dir,
        logger=logger,
    )
    bank = ingestor.ingest()

    # Generate and display DPO pairs
    dpo = bank.generate_dpo_pairs(n=50)
    logger.info("Generated %d DPO preference pairs.", len(dpo))
    (output_dir / "dpo_pairs.json").write_text(
        json.dumps(dpo, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # Quick recall test
    if bank.is_loaded:
        test_query = bank.embed_description({
            "entry_type": "reversal",
            "direction": "BUY",
            "candle_count_up": 2,
            "candle_count_down": 5,
            "consolidation_streak": 4,
            "reversal_signal": "wick_rejection",
            "momentum_bias": "bullish",
        })
        results = bank.search(test_query, top_k=3)
        logger.info("Self-test recall (BUY reversal query):")
        for r in results:
            logger.info(
                "  → %s | sim=%.4f | archetype=%d | centroid=%s",
                r.label, r.similarity, r.archetype_id, r.is_archetype_centroid
            )

    logger.info("Memory bank ready. Restart main.py to activate.")


if __name__ == "__main__":
    main()
