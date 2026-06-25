from __future__ import annotations

import os
import pickle
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PIL import Image


class _NullLogger:
    def info(self, *args: object, **kwargs: object) -> None:
        pass

    def warning(self, *args: object, **kwargs: object) -> None:
        pass

    def exception(self, *args: object, **kwargs: object) -> None:
        pass

    def error(self, *args: object, **kwargs: object) -> None:
        pass

    def debug(self, *args: object, **kwargs: object) -> None:
        pass


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _extract_features_for_test(image: Image.Image) -> NDArray[np.float32]:
    img = image.convert("RGB")
    arr = np.asarray(img.resize((64, 64), Image.Resampling.BILINEAR), dtype=np.float32) / 255.0
    means = arr.mean(axis=(0, 1)).astype(np.float32)
    stds = arr.std(axis=(0, 1)).astype(np.float32)
    flat = arr.reshape(-1).astype(np.float32)
    feat = np.concatenate([flat, means, stds], axis=0).astype(np.float32)
    return feat


def test_cv_memory_classifier_accuracy_on_memory_bank() -> None:
    from phoenixguard.core.config import MEMORY_BANK, RUNTIME

    model_path = _repo_root() / "models" / "cv_memory_direction.pkl"
    assert model_path.exists(), f"Missing tuned classifier: {model_path}"

    with model_path.open("rb") as f:
        payload = pickle.load(f)

    clf = payload.get("clf", None)
    assert clf is not None, "Serialized classifier payload has no 'clf'"

    buy_dir = RUNTIME.project_root / MEMORY_BANK.buys_dir
    sell_dir = RUNTIME.project_root / MEMORY_BANK.sells_dir
    buy_files = sorted([p for p in buy_dir.glob("*") if p.is_file()])
    sell_files = sorted([p for p in sell_dir.glob("*") if p.is_file()])

    assert buy_files and sell_files, "BUY/SELL memory folders must contain images"

    x_list: list[NDArray[np.float32]] = []
    y_true: list[int] = []

    for p in buy_files:
        img = Image.open(p).convert("RGB")
        x_list.append(_extract_features_for_test(img))
        y_true.append(1)

    for p in sell_files:
        img = Image.open(p).convert("RGB")
        x_list.append(_extract_features_for_test(img))
        y_true.append(0)

    X = np.stack(x_list, axis=0).astype(np.float32)
    y = np.array(y_true, dtype=np.int32)

    y_hat: NDArray[np.int32] = np.asarray(clf.predict(X), dtype=np.int32)
    acc = float(int(np.sum(y_hat == y))) / float(y.size)

    # Real-world baseline guardrail: require strong in-domain memory performance.
    assert acc >= 0.80, f"cv_memory_direction.pkl accuracy too low: {acc:.3f}"


def test_rl_policy_memory_boost_path_is_active() -> None:
    from phoenixguard.decision.rl_module import RLPolicyEngine

    engine = RLPolicyEngine(logger=_NullLogger(), in_dim=64, mcts_sims=10)
    state = np.zeros((64,), dtype=np.float32)

    out_no_boost = engine.infer(state, memory_recall_top1_sim=0.20, memory_recall_direction="BUY")
    out_boost = engine.infer(state, memory_recall_top1_sim=0.95, memory_recall_direction="BUY")

    assert out_boost.boost_applied is True
    assert out_no_boost.boost_applied is False
    assert out_boost.boosted_action in {"BUY", "SELL", "HOLD"}

    p_buy_no = float(out_no_boost.probs["BUY"])
    p_buy_yes = float(out_boost.probs["BUY"])
    assert p_buy_yes >= p_buy_no, "Memory boost should not reduce BUY probability when direction is BUY"


def test_image_fusion_regressor_operates_without_ohlc() -> None:
    from phoenixguard.decision.regression_module import ForecastRouter

    old_mode = os.environ.get("PHOENIXGUARD_FORECAST_ENGINE")
    os.environ["PHOENIXGUARD_FORECAST_ENGINE"] = "IMAGE_FUSION"
    router = ForecastRouter(model_name="amazon/chronos-2", logger=_NullLogger(), max_interval_pct=0.40)

    chart_state: dict[str, Any] = {
        "direction": "BUY",
        "direction_probability": 0.71,
        "implied_3min_move_pct": 0.18,
        "entry_candle": {"body_pct": 0.24, "upper_wick_pct": 0.10, "lower_wick_pct": 0.31, "color": "green"},
        "mcts": {"buy_prob": 0.68, "sell_prob": 0.32},
    }
    detections: list[dict[str, Any]] = [
        {"pattern": "buy_memory_bias", "confidence": 0.93, "bbox": [0, 0, 10, 10]},
        {"pattern": "reversal", "confidence": 0.62, "bbox": [5, 5, 25, 25]},
    ]

    try:
        out = router.forecast_3m(
            chart_state,
            quantiles=(0.05, 0.5, 0.95),
            detections=detections,
            memory_similarity=0.92,
            memory_direction="BUY",
        )
    finally:
        if old_mode is None:
            os.environ.pop("PHOENIXGUARD_FORECAST_ENGINE", None)
        else:
            os.environ["PHOENIXGUARD_FORECAST_ENGINE"] = old_mode

    for key in ("q05", "q50", "q95", "point", "force_hold", "ad_indicator", "poly_slope", "poly_r2"):
        assert key in out

    assert out["q05"] <= out["q95"]
    assert -1.0 <= out["ad_indicator"] <= 1.0


def test_conformal_interval_math_component_is_operational() -> None:
    from phoenixguard.decision.regression_module import conformal_interval

    rng = np.random.default_rng(808)
    returns = rng.normal(loc=0.04, scale=0.18, size=64).astype(np.float32)
    lo, hi = conformal_interval(returns, alpha=0.05)

    assert np.isfinite(lo)
    assert np.isfinite(hi)
    assert lo <= hi
