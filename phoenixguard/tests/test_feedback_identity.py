from __future__ import annotations

import sys
from pathlib import Path

import numpy as np


_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.decision.rl_module import RLPolicyEngine, RLResult
from phoenixguard.runtime.adaptive_runtime import ContinualLearningManager


class _NullLogger:
    def info(self, *args: object, **kwargs: object) -> None:
        return None

    def warning(self, *args: object, **kwargs: object) -> None:
        return None

    def exception(self, *args: object, **kwargs: object) -> None:
        return None

    def error(self, *args: object, **kwargs: object) -> None:
        return None


def test_continual_learning_uses_context_id_not_image_hash(tmp_path: Path) -> None:
    manager = ContinualLearningManager(tmp_path, _NullLogger())

    manager.record_inference_context(
        image_hash="same-image",
        context_id="inf-a",
        context_key="ctx-a",
        context_descriptor="first inference",
        local_ensemble={"ensemble": {"champion_model": "m1", "confirmer_model": "m2"}},
        predicted_action="BUY",
        confidence=0.81,
        style_signature={"dark_theme": 1.0},
    )
    manager.record_inference_context(
        image_hash="same-image",
        context_id="inf-b",
        context_key="ctx-b",
        context_descriptor="second inference",
        local_ensemble={"ensemble": {"champion_model": "m3", "confirmer_model": "m4"}},
        predicted_action="SELL",
        confidence=0.77,
        style_signature={"dark_theme": 1.0},
    )

    first = manager.record_feedback("same-image", "BUY", "first outcome", context_id="inf-a", submission_id="sub-a")
    second = manager.record_feedback("same-image", "SELL", "second outcome", context_id="inf-b", submission_id="sub-b")

    assert first["context_id"] == "inf-a"
    assert second["context_id"] == "inf-b"
    assert first["context_key"] == "ctx-a"
    assert second["context_key"] == "ctx-b"


def test_rl_pending_contexts_use_context_id_not_image_hash(tmp_path: Path) -> None:
    engine = RLPolicyEngine(
        logger=_NullLogger(),
        in_dim=4,
        mcts_sims=2,
        state_path=tmp_path / "policy.pt",
        feedback_buffer_path=tmp_path / "feedback.jsonl",
        pending_contexts_path=tmp_path / "pending.json",
    )
    policy_result = RLResult(
        probs={"BUY": 0.6, "SELL": 0.2, "HOLD": 0.2},
        mcts_value=0.1,
        prior_probs={"BUY": 0.6, "SELL": 0.2, "HOLD": 0.2},
        policy_probs={"BUY": 0.6, "SELL": 0.2, "HOLD": 0.2},
        policy_action="BUY",
        blend_weight=0.1,
    )

    engine.record_inference_context(
        image_hash="same-image",
        context_id="inf-a",
        state_vec=np.zeros((4,), dtype=np.float32),
        prior_probs={"BUY": 0.6, "SELL": 0.2, "HOLD": 0.2},
        policy_result=policy_result,
        predicted_action="BUY",
    )
    engine.record_inference_context(
        image_hash="same-image",
        context_id="inf-b",
        state_vec=np.ones((4,), dtype=np.float32),
        prior_probs={"BUY": 0.2, "SELL": 0.6, "HOLD": 0.2},
        policy_result=policy_result,
        predicted_action="SELL",
    )

    first = engine.record_feedback("same-image", "BUY", "first outcome", context_id="inf-a", submission_id="sub-a")
    second = engine.record_feedback("same-image", "SELL", "second outcome", context_id="inf-b", submission_id="sub-b")

    assert first["context_id"] == "inf-a"
    assert second["context_id"] == "inf-b"
    assert first["predicted_action"] == "BUY"
    assert second["predicted_action"] == "SELL"
