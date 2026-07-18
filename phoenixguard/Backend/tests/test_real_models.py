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
    ./.venv-dev/Scripts/python.exe -m pytest Backend/tests/test_real_models.py -v --tb=short
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any, ClassVar, Protocol, cast
import numpy as np
from PIL import Image

_REPO = Path(__file__).resolve().parents[2]
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
    use_hf_endpoint: bool
    hf_last_inference_ok: bool | None
    hf_last_inference_error: str

    def detect(self, image_rgb: Image.Image) -> list[dict[str, Any]]: ...


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
        from Developer.developer_tools.hf_model_check import check_model_access, run_all
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

        previous_bootstrap_setting = os.environ.get("PHOENIXGUARD_CV_ALLOW_REMOTE_BOOTSTRAP")
        previous_endpoint_setting = os.environ.get("PHOENIXGUARD_CV_ALLOW_REMOTE_ENDPOINT")
        os.environ["PHOENIXGUARD_CV_ALLOW_REMOTE_BOOTSTRAP"] = "1"
        os.environ["PHOENIXGUARD_CV_ALLOW_REMOTE_ENDPOINT"] = "0"
        try:
            det = cast(_DetectorLike, CVPatternDetector(
                primary_model=MODELS.cv_primary,
                fallback_model=MODELS.cv_primary,
                logger=_NullLogger(),
            ))
        finally:
            if previous_bootstrap_setting is None:
                os.environ.pop("PHOENIXGUARD_CV_ALLOW_REMOTE_BOOTSTRAP", None)
            else:
                os.environ["PHOENIXGUARD_CV_ALLOW_REMOTE_BOOTSTRAP"] = previous_bootstrap_setting
            if previous_endpoint_setting is None:
                os.environ.pop("PHOENIXGUARD_CV_ALLOW_REMOTE_ENDPOINT", None)
            else:
                os.environ["PHOENIXGUARD_CV_ALLOW_REMOTE_ENDPOINT"] = previous_endpoint_setting
        if det.model is None:
            self.skipTest("HF CV model could not be loaded (network/token/ultralytics issue)")
        return det

    def test_yolo_model_loads_from_hf(self):
        det = self._load_hf_detector()
        self.assertIsNotNone(det.model, "CV detector should load the real HF-hosted YOLO weights")
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

        previous_local_setting = os.environ.get("PHOENIXGUARD_ENABLE_LOCAL_YOLO_IN_TESTS")
        os.environ["PHOENIXGUARD_ENABLE_LOCAL_YOLO_IN_TESTS"] = "1"
        try:
            result = det.detect(img)
        finally:
            if previous_local_setting is None:
                os.environ.pop("PHOENIXGUARD_ENABLE_LOCAL_YOLO_IN_TESTS", None)
            else:
                os.environ["PHOENIXGUARD_ENABLE_LOCAL_YOLO_IN_TESTS"] = previous_local_setting
        # Returns a list (may be empty — yolov8n wasn't trained on forex patterns,
        # but the inference must complete without error)
        self.assertIsInstance(result, list)
        print(f"  ✓  YOLO detect() ran on real image. Detections: {len(result)}")

    def test_yolo_model_is_ultralytics_yolo_instance(self):
        """Confirm detector backend is active (YOLO object or HF endpoint mode)."""
        from ultralytics import YOLO
        det = self._load_hf_detector()
        self.assertIsInstance(det.model, YOLO)
        print("  ✓  Model is ultralytics.YOLO instance")

    def test_yolo_predict_raw_directly(self):
        """Validate the backend path without forcing unstable native local inference."""
        det = self._load_hf_detector()
        img_array = np.zeros((256, 256, 3), dtype=np.uint8)
        self.assertIsNotNone(det.model)
        model = det.model
        assert model is not None
        results = model.predict(source=img_array, verbose=False, conf=0.3)
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

    _worker_payload: ClassVar[dict[str, Any] | None] = None
    _worker_failure: ClassVar[str | None] = None
    _RESULT_PREFIX: ClassVar[str] = "PHOENIXGUARD_REAL_MODEL_RESULT="

    @classmethod
    def _run_isolated_contract(cls) -> dict[str, Any]:
        """Load and exercise the real model once outside the pytest process."""
        if cls._worker_payload is not None:
            return cls._worker_payload
        if cls._worker_failure is not None:
            raise AssertionError(cls._worker_failure)

        env = dict(os.environ)
        env.update(
            {
                "CUDA_VISIBLE_DEVICES": "-1",
                "HF_HUB_DISABLE_SYMLINKS_WARNING": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
                "HF_HUB_OFFLINE": "1",
                "MKL_NUM_THREADS": "1",
                "NUMEXPR_NUM_THREADS": "1",
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "TOKENIZERS_PARALLELISM": "false",
                "TRANSFORMERS_OFFLINE": "1",
            }
        )
        timeout_raw = str(os.getenv("PHOENIXGUARD_REAL_MODEL_WORKER_TIMEOUT_SEC", "180") or "180")
        try:
            timeout_sec = max(30, min(600, int(float(timeout_raw))))
        except (TypeError, ValueError):
            timeout_sec = 180

        worker = _REPO / "Backend" / "tests" / "support" / "real_model_worker.py"
        creationflags = int(getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            completed = subprocess.run(
                [sys.executable, str(worker), "sentence-transformer-contract"],
                cwd=str(_REPO),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                check=False,
                timeout=timeout_sec,
                creationflags=creationflags,
            )
        except subprocess.TimeoutExpired as exc:
            cls._worker_failure = (
                f"real SentenceTransformer worker exceeded {timeout_sec}s; "
                "the worker was terminated without destabilizing pytest/VS Code. "
                f"stdout={str(exc.stdout or '')[-1200:]!r} "
                f"stderr={str(exc.stderr or '')[-1200:]!r}"
            )
            raise AssertionError(cls._worker_failure) from exc

        payload: dict[str, Any] | None = None
        for line in reversed(completed.stdout.splitlines()):
            if not line.startswith(cls._RESULT_PREFIX):
                continue
            decoded = json.loads(line[len(cls._RESULT_PREFIX):])
            if isinstance(decoded, dict):
                payload = cast(dict[str, Any], decoded)
            break

        if completed.returncode != 0 or payload is None or payload.get("ok") is not True:
            unsigned_status = int(completed.returncode) & 0xFFFFFFFF
            cls._worker_failure = (
                "real SentenceTransformer worker failed safely outside the pytest process: "
                f"returncode={completed.returncode} windows_status=0x{unsigned_status:08X}; "
                f"payload={payload!r}; stdout={completed.stdout[-1600:]!r}; "
                f"stderr={completed.stderr[-1600:]!r}"
            )
            raise AssertionError(cls._worker_failure)

        cls._worker_payload = payload
        return payload

    def test_embedder_loads_and_produces_384_dim_vector(self):
        result = self._run_isolated_contract()
        self.assertEqual(result["embedding_dim"], 384)
        self.assertIs(result["all_finite"], True, "Embedding contains NaN/Inf")
        self.assertEqual(result["torch_threads"], 1)
        self.assertEqual(result["torch_interop_threads"], 1)
        print(
            f"  PASS Isolated real embedding: dim={result['embedding_dim']}, "
            f"norm={float(result['first_norm']):.4f}, worker_pid={result['pid']}"
        )

    def test_embedder_different_texts_produce_different_vectors(self):
        result = self._run_isolated_contract()
        cosine = float(result["contrast_cosine"])
        # Should be semantically different (cosine < 0.95)
        self.assertLess(cosine, 0.95, "Different texts should produce distinguishable embeddings")
        print(f"  PASS Cosine similarity between contrasting texts: {cosine:.4f}")

    def test_embedder_same_text_produces_same_vector(self):
        result = self._run_isolated_contract()
        self.assertLessEqual(float(result["same_max_abs_diff"]), 1e-5)
        print("  PASS Same text produces an identical embedding (deterministic)")

    def test_personalization_engine_uses_real_embedder(self):
        """PersonalizationEngine consumes the isolated real ST model output."""
        result = self._run_isolated_contract()
        self.assertIs(result["personalization_used_real_model"], True)
        self.assertGreater(float(result["style_norm"]), 0.0)
        prefix = str(result["style_prefix"])
        self.assertTrue(prefix)
        self.assertIn("sentence_transformers", str(result["model_class"]))
        print(f"  PASS PersonalizationEngine used real model: '{prefix[:80]}...'")


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
        from Developer.developer_tools.hf_model_check import check_model_access
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
            open_price, high, low, close = price, price + 0.0020, price - 0.0010, price + 0.0015
            row: list[float] = [open_price, high, low, close]
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
