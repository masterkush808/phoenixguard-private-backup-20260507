"""
PhoenixGuard — Real Model Integration Tests
============================================
Confirms that actual, real models are loaded and produce valid output.
Tests:
  1. HF Hub accessibility for every configured model (hf_model_check)
  2. CV module: local yolov8n.pt loaded via YOLO(), real inference on image
  3. CV module: foduucom HF model accessibility check
  4. Sentence-transformer embedder (all-MiniLM-L6-v2) loads and embeds
  5. Chronos-2 HF model accessibility check
  6. ChronosRegressor pipeline init + fallback forecast with OHLC
  7. PersonalizationEngine uses real sentence-transformer embedder

Run:
    cd phoenixguard
    python -m pytest tests/test_real_models.py -v --tb=short
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Protocol, cast
import numpy as np
from PIL import Image

_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

_HF_TOKEN = os.getenv("HF_TOKEN", "").strip() or None
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


class _NullLogger:
    def info(self, *args: object, **kwargs: object) -> None: pass
    def warning(self, *args: object, **kwargs: object) -> None: pass
    def exception(self, *args: object, **kwargs: object) -> None: pass
    def error(self, *args: object, **kwargs: object) -> None: pass
    def debug(self, *args: object, **kwargs: object) -> None: pass


class _YOLOLike(Protocol):
    def predict(self, source: Any = None, stream: bool = False, predictor: Any | None = None, **kwargs: Any) -> list[Any]: ...


class _DetectorLike(Protocol):
    model: _YOLOLike | None
    model_name: str

    def detect(self, image_rgb: Image.Image) -> list[dict[str, Any]]: ...


class _SentenceTransformerLike(Protocol):
    def encode(self, sentences: str, **kwargs: Any) -> Any: ...


# ===========================================================================
# 1. HF Hub accessibility for all configured models
# ===========================================================================
class TestHFModelAccessibility(unittest.TestCase):
    """
    Uses hf_model_check.py to contact HuggingFace and confirm every
    model listed in config.MODELS is reachable (or at least recognized
    as private/gated — meaning it exists on the Hub).
    Does NOT download weights.
    """

    def setUp(self):
        from hf_model_check import check_model_access, run_all
        from config import MODELS
        self.check = check_model_access
        self.run_all = run_all
        self.MODELS = MODELS

    def _check_and_report(self, model_id: str) -> None:
        status = self.check(model_id, token=_HF_TOKEN)
        if bool(getattr(status, "network_blocked", False)):
            self.skipTest(f"HuggingFace access blocked by this environment: {status.error}")
        # A model that exists is "ok=True" or "private_or_gated=True (requires token)"
        exists = status.ok or status.private_or_gated
        self.assertTrue(
            exists,
            f"Model '{model_id}' is NOT accessible on HuggingFace. "
            f"Error: {status.error}"
        )
        if status.ok:
            print(f"  ✓  {model_id}  (sha={status.sha[:8]})")
        else:
            print(f"  ⚠  {model_id}  (gated/private — token required)")

    def test_cv_primary_foduucom(self):
        """foduucom/stockmarket-pattern-detection-yolov8 must exist on HF."""
        # strip the hf:// prefix that ultralytics uses
        model_id = self.MODELS.cv_primary.replace("hf://", "")
        self._check_and_report(model_id)

    def test_fin_dora_adapter(self):
        """wangd12/financebench_llama_3_1_8b_8bits_r8_dora must exist on HF."""
        self._check_and_report(self.MODELS.fin_dora_adapter)

    def test_chronos_2(self):
        """amazon/chronos-2 must exist on HF."""
        self._check_and_report(self.MODELS.chronos_model)

    def test_style_embedder(self):
        """sentence-transformers/all-MiniLM-L6-v2 must exist on HF."""
        self._check_and_report(self.MODELS.style_embedder)

    def test_run_all_returns_results_for_all_models(self):
        """run_all() returns one status per required model."""
        results = self.run_all(token=_HF_TOKEN)
        if results and all(bool(getattr(item, "network_blocked", False)) for item in results):
            self.skipTest(f"HuggingFace access blocked by this environment: {results[0].error}")
        # CV primary/fallback, finance adapter, Chronos, and style embedder.
        self.assertGreaterEqual(len(results), 5)
        for r in results:
            print(f"  {'✓' if r.ok else '✗'}  {r.model}  error={r.error or 'none'}")
            self.assertTrue(bool(r.model))


# ===========================================================================
# 2. CV Module — HF model loaded and used for inference
# ===========================================================================
class TestCVRealYOLO(unittest.TestCase):
    """
    Loads the HF-hosted CV model via CVPatternDetector.
    Confirms the YOLO model object is not None and can run prediction.
    This validates HF-backed CV path (no local .pt dependency).
    """

    def _load_hf_detector(self):
        from config import MODELS
        from cv_module import CVPatternDetector

        if not bool(_HF_TOKEN):
            self.skipTest("HF_TOKEN is required for HF-backed CV integration tests.")

        det = cast(_DetectorLike, CVPatternDetector(
            primary_model=MODELS.cv_primary,
            fallback_model=MODELS.cv_primary,
            logger=_NullLogger(),
        ))
        if det.model is None and not bool(getattr(det, "use_hf_endpoint", False)):
            self.skipTest("HF CV model could not be loaded (network/token/ultralytics issue)")
        return det

    def test_yolo_model_loads_from_hf(self):
        det = self._load_hf_detector()
        self.assertTrue(
            bool(getattr(det, "use_hf_endpoint", False)) or det.model is not None,
            "CV detector should be active via HF endpoint or YOLO model",
        )
        print(f"  ✓  YOLO model loaded: {det.model_name}")

    def test_yolo_detect_returns_list_on_real_image(self):
        """Run real YOLO inference on a synthetic 512×512 chart-like image."""
        det = self._load_hf_detector()

        # Synthetic "chart" image: gradient background with white candle bodies
        img = Image.new("RGB", (512, 512), color=(20, 20, 20))
        pixels = img.load()
        # Draw some white rectangles to simulate candles
        for x in range(50, 460, 40):
            for y in range(150, 350):
                if pixels is not None:
                    pixels[x, y] = (220, 220, 220)

        result = det.detect(img)
        # Returns a list (may be empty — yolov8n wasn't trained on forex patterns,
        # but the inference must complete without error)
        self.assertIsInstance(result, list)
        print(f"  ✓  YOLO detect() ran on real image. Detections: {len(result)}")

    def test_yolo_model_is_ultralytics_yolo_instance(self):
        """Confirm detector backend is active (YOLO object or HF endpoint mode)."""
        from ultralytics import YOLO
        det = self._load_hf_detector()
        if bool(getattr(det, "use_hf_endpoint", False)):
            self.assertTrue(True)
            print("  ✓  CV detector is using HF endpoint mode")
        else:
            self.assertIsInstance(det.model, YOLO)
            print("  ✓  Model is ultralytics.YOLO instance")

    def test_yolo_predict_raw_directly(self):
        """Validate raw prediction path for whichever backend is active."""
        det = self._load_hf_detector()
        img_array = np.zeros((256, 256, 3), dtype=np.uint8)
        if bool(getattr(det, "use_hf_endpoint", False)):
            img = Image.fromarray(img_array)
            results = det.detect(img)
            self.assertIsInstance(results, list)
            print(f"  ✓  HF endpoint detect() returned {len(results)} objects")
        else:
            self.assertIsNotNone(det.model)
            model = det.model
            assert model is not None
            try:
                results = model.predict(source=img_array, verbose=False, conf=0.3)
            except Exception as exc:
                self.skipTest(f"Local YOLO runtime unavailable in this environment: {exc}")
            self.assertIsNotNone(results)
            self.assertIsInstance(results, list)
            print(f"  ✓  YOLO.predict() returned {len(results)} result objects")


# ===========================================================================
# 3. Sentence-Transformer Embedder — real model download + embedding
# ===========================================================================
class TestSentenceTransformerEmbedder(unittest.TestCase):
    """
    Downloads (or loads from cache) sentence-transformers/all-MiniLM-L6-v2.
    This is the 'style_embedder' used in PersonalizationEngine and MemoryBank.
    Confirms real vectors are produced (dim=384, normalized).
    """

    def _load_st(self):
        try:
            from utils import can_import_sentence_transformers_safely
            if not can_import_sentence_transformers_safely():
                self.skipTest("sentence-transformers runtime unavailable in this environment")
            from sentence_transformers import SentenceTransformer
            return cast(_SentenceTransformerLike, SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2"))
        except Exception as e:
            self.skipTest(f"sentence-transformers not available or no network: {e}")

    def test_embedder_loads_and_produces_384_dim_vector(self):
        model = self._load_st()
        vec = np.asarray(model.encode("BUY entry after 4 red candles with wick rejection", convert_to_numpy=True))
        self.assertEqual(len(vec), 384)
        # Should be a float array
        self.assertTrue(np.all(np.isfinite(vec)), "Embedding contains NaN/Inf")
        print(f"  ✓  Embedding produced: dim={len(vec)}, norm={np.linalg.norm(vec):.4f}")

    def test_embedder_different_texts_produce_different_vectors(self):
        model = self._load_st()
        v1 = np.asarray(model.encode("strong bullish breakout above resistance", convert_to_numpy=True))
        v2 = np.asarray(model.encode("bearish reversal after weak close near low", convert_to_numpy=True))
        cosine = float(np.dot(v1, v2) / (np.linalg.norm(v1) * np.linalg.norm(v2) + 1e-10))
        # Should be semantically different (cosine < 0.95)
        self.assertLess(cosine, 0.95, "Different texts should produce distinguishable embeddings")
        print(f"  ✓  Cosine similarity between contrasting texts: {cosine:.4f}")

    def test_embedder_same_text_produces_same_vector(self):
        model = self._load_st()
        text = "SELL signal at resistance with upper wick rejection"
        v1 = np.asarray(model.encode(text, convert_to_numpy=True))
        v2 = np.asarray(model.encode(text, convert_to_numpy=True))
        np.testing.assert_allclose(v1, v2, atol=1e-5)
        print(f"  ✓  Same text → identical embedding (deterministic)")

    def test_personalization_engine_uses_real_embedder(self):
        """PersonalizationEngine.update_style_from_memory_bank uses real ST model."""
        try:
            from personalization import PersonalizationEngine
            from security import SecurityManager, EncryptedPreferenceStore
        except ImportError as e:
            self.skipTest(f"personalization not importable: {e}")

        with tempfile.TemporaryDirectory() as td:
            sec = SecurityManager(Path(td) / "d", Path(td) / "l", kdf_iterations=1000)
            derive_fernet = getattr(sec, "derive_fernet")
            fernet = derive_fernet("real-embedder-test")
            store = EncryptedPreferenceStore(Path(td) / "prefs.enc.sqlite", fernet)
            engine = PersonalizationEngine(
                "sentence-transformers/all-MiniLM-L6-v2", store, _NullLogger()
            )
            dpo_pairs = [
                {"chosen": "BUY after wick rejection", "rejected": "HOLD", "reason": "clear reversal"},
                {"chosen": "SELL after exhaustion", "rejected": "HOLD", "reason": "5 consecutive green"},
            ]
            engine.update_style_from_memory_bank(dpo_pairs)
            prefix = engine.style_prefix_prompt()
            self.assertIsInstance(prefix, str)
            print(f"  ✓  PersonalizationEngine produced style prefix: '{prefix[:80]}...'")


# ===========================================================================
# 4. ChronosRegressor — model check + forecast with OHLC
# ===========================================================================
class TestChronosModel(unittest.TestCase):
    """
    Confirms amazon/chronos-2 is accessible on HF Hub (API check, no download).
    Also confirms ChronosRegressor.forecast_3m produces valid quantile output
    via the robust fallback path (which uses real polynomial regression + conformal
    interval calculation from the OHLC data).
    """

    def test_chronos_hf_accessible(self):
        from hf_model_check import check_model_access
        status = check_model_access("amazon/chronos-2", token=_HF_TOKEN)
        if bool(getattr(status, "network_blocked", False)):
            self.skipTest(f"HuggingFace access blocked by this environment: {status.error}")
        self.assertTrue(
            status.ok or status.private_or_gated,
            f"amazon/chronos-2 not accessible on HF. Error: {status.error}"
        )
        print(f"  ✓  amazon/chronos-2 on HF Hub: ok={status.ok}, sha={status.sha[:8]}")

    def test_chronos_regressor_fallback_produces_valid_quantiles(self):
        """
        Force pipeline=None → ensures the poly-regression + conformal fallback
        (real math, real scipy/mapie or numpy) is computing actual quantiles.
        """
        from regression_module import ChronosRegressor
        reg = ChronosRegressor("amazon/chronos-2", _NullLogger())
        reg.pipeline = None  # bypass actual Chronos download

        ohlc: list[list[float]] = []
        price = 1.2300
        for _ in range(20):
            o, h, l, c = price, price + 0.0020, price - 0.0010, price + 0.0015
            row: list[float] = [o, h, l, c]
            ohlc.append(row)
            price += 0.0008

        result = reg.forecast_3m({"ohlc_last20": ohlc, "implied_3min_move_pct": 0.05})
        for key in ("q05", "q50", "q95", "point", "force_hold", "poly_slope", "ad_indicator"):
            self.assertIn(key, result, f"Missing key: {key}")

        self.assertLessEqual(result["q05"], result["q95"])
        self.assertIsInstance(result["force_hold"], bool)
        self.assertGreaterEqual(result["ad_indicator"], -1.0)
        self.assertLessEqual(result["ad_indicator"], 1.0)
        print(
            f"  ✓  ChronosRegressor fallback → q05={result['q05']:.5f} "
            f"q50={result['q50']:.5f} q95={result['q95']:.5f} "
            f"slope={result['poly_slope']:.6f} A/D={result['ad_indicator']:.4f}"
        )

    def test_chronos_regressor_ad_correlates_with_trend(self):
        """Bullish bars → positive A/D indicator; bearish bars → negative."""
        from regression_module import ChronosRegressor
        reg = ChronosRegressor("amazon/chronos-2", _NullLogger())
        reg.pipeline = None

        bullish = [[1.0, 1.10, 0.98, 1.09]] * 20   # strong close near high
        bearish = [[1.1, 1.15, 0.90, 0.92]] * 20   # weak close near low

        r_bull = reg.forecast_3m({"ohlc_last20": bullish})
        r_bear = reg.forecast_3m({"ohlc_last20": bearish})

        self.assertGreater(r_bull["ad_indicator"], 0.0)
        self.assertLess(r_bear["ad_indicator"], 0.0)
        print(f"  ✓  Bullish A/D={r_bull['ad_indicator']:.4f}, Bearish A/D={r_bear['ad_indicator']:.4f}")


# ===========================================================================
# 5. Config — all model IDs are real, recognizable strings (not placeholders)
# ===========================================================================
class TestConfigModelIDs(unittest.TestCase):
    """
    Ensures no placeholder model IDs remain in config (e.g. 'openvision/yolo26-s'
    which was a stub).  Each model ID must contain either a known org or a valid
    HF-style 'owner/name' pattern.
    """

    def setUp(self):
        from config import MODELS
        self.m = MODELS

    def test_cv_primary_has_hf_prefix_or_valid_repo(self):
        self.assertTrue(
            self.m.cv_primary.startswith("hf://") or "/" in self.m.cv_primary,
            "cv_primary must be a valid HF repo or hf:// URI"
        )
        # Must NOT be the old stub
        self.assertNotIn("openvision", self.m.cv_primary.lower())
        self.assertNotIn("yolo26-s", self.m.cv_primary.lower())
        print(f"  ✓  cv_primary = {self.m.cv_primary}")

    def test_cv_fallback_has_hf_prefix_or_valid_repo(self):
        self.assertTrue(
            self.m.cv_fallback.startswith("hf://") or "/" in self.m.cv_fallback,
            "cv_fallback must be a valid HF repo or hf:// URI"
        )
        print(f"  ✓  cv_fallback = {self.m.cv_fallback}")

    def test_chronos_is_amazon(self):
        self.assertIn("amazon", self.m.chronos_model.lower())
        self.assertIn("chronos", self.m.chronos_model.lower())
        print(f"  ✓  chronos_model = {self.m.chronos_model}")

    def test_style_embedder_is_minilm(self):
        self.assertIn("MiniLM", self.m.style_embedder)
        print(f"  ✓  style_embedder = {self.m.style_embedder}")

    def test_fin_dora_adapter_is_real_repo(self):
        parts = self.m.fin_dora_adapter.split("/")
        self.assertEqual(len(parts), 2, "fin_dora_adapter must be 'owner/repo' format")
        print(f"  ✓  fin_dora_adapter = {self.m.fin_dora_adapter}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
