from __future__ import annotations

import json
import tempfile
from pathlib import Path
import sys
from typing import Any, cast

import numpy as np


_REPO = Path(__file__).resolve().parents[2]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from phoenixguard.decision.rl_module import RLPolicyEngine


class _NullLogger:
    def info(self, *args: object, **kwargs: object) -> None:
        return None

    def warning(self, *args: object, **kwargs: object) -> None:
        return None


def test_rl_prior_anchor_holds_before_feedback() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        engine = RLPolicyEngine(
            logger=_NullLogger(),
            in_dim=64,
            mcts_sims=5,
            state_path=root / "rl_state.pt",
            feedback_buffer_path=root / "rl_feedback.jsonl",
            pending_contexts_path=root / "rl_pending.json",
        )
        result = engine.infer(
            np.zeros((64,), dtype=np.float32),
            prior_probs={"BUY": 0.90, "SELL": 0.05, "HOLD": 0.05},
            module_reliability={"cv_quality": 0.9, "structure_consistency": 0.9, "sequence_clarity": 0.8, "memory_novelty": 0.1},
        )

        assert float(result.blend_weight) == 0.0
        assert float(result.probs["BUY"]) > 0.80
        assert bool(result.contribution_gate_open) is False


def test_rl_contribution_gate_opens_only_after_verified_accuracy() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        engine = RLPolicyEngine(
            logger=_NullLogger(),
            in_dim=64,
            mcts_sims=5,
            state_path=root / "rl_state.pt",
            feedback_buffer_path=root / "rl_feedback.jsonl",
            pending_contexts_path=root / "rl_pending.json",
        )

        for idx in range(4):
            engine.append_feedback_item(  # noqa: SLF001
                {
                    "state": np.full((64,), fill_value=float(idx + 1) / 10.0, dtype=np.float32).tolist(),
                    "target_action": "BUY",
                    "policy_action": "BUY",
                    "prior_probs": {"BUY": 0.34, "SELL": 0.33, "HOLD": 0.33},
                    "reward": 1.0,
                }
            )

        result = engine.infer(
            np.ones((64,), dtype=np.float32),
            prior_probs={"BUY": 0.34, "SELL": 0.33, "HOLD": 0.33},
            module_reliability={"cv_quality": 0.9, "structure_consistency": 0.9, "sequence_clarity": 0.9, "memory_novelty": 0.1},
        )
        stats = engine.stats_snapshot()

        assert bool(result.contribution_gate_open) is True
        assert float(result.rolling_accuracy) >= 0.80
        assert float(result.blend_weight) > 0.0
        assert bool(stats["contribution_gate_open"]) is True


def test_rl_feedback_creates_verified_update_path() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        engine = RLPolicyEngine(
            logger=_NullLogger(),
            in_dim=64,
            mcts_sims=5,
            state_path=root / "rl_state.pt",
            feedback_buffer_path=root / "rl_feedback.jsonl",
            pending_contexts_path=root / "rl_pending.json",
        )

        feedback: dict[str, Any] | None = None
        for idx in range(4):
            state_vec = np.full((64,), fill_value=float(idx + 1) / 10.0, dtype=np.float32)
            rl_out = engine.infer(
                state_vec,
                prior_probs={"BUY": 0.34, "SELL": 0.33, "HOLD": 0.33},
                module_reliability={"cv_quality": 0.8, "structure_consistency": 0.75, "sequence_clarity": 0.7, "memory_novelty": 0.2},
            )
            engine.record_inference_context(
                image_hash=f"img_{idx}",
                state_vec=state_vec,
                prior_probs={"BUY": 0.34, "SELL": 0.33, "HOLD": 0.33},
                policy_result=rl_out,
                predicted_action="BUY",
                memory_recall_top1_sim=0.92,
                memory_recall_direction="BUY",
                module_reliability={"cv_quality": 0.8},
            )
            feedback = engine.record_feedback(f"img_{idx}", "BUY", "clean continuation")

        assert feedback is not None
        assert cast(bool, feedback["updated"]) is True
        assert cast(int, feedback["feedback_count"]) >= 4
        assert cast(int, feedback["online_update_count"]) >= 1
        assert (root / "rl_state.pt").exists()
        assert (root / "rl_feedback.jsonl").exists()


def test_rl_feedback_carries_feedback_image_metadata() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        engine = RLPolicyEngine(
            logger=_NullLogger(),
            in_dim=64,
            mcts_sims=5,
            state_path=root / "rl_state.pt",
            feedback_buffer_path=root / "rl_feedback.jsonl",
            pending_contexts_path=root / "rl_pending.json",
        )

        state_vec = np.full((64,), fill_value=0.25, dtype=np.float32)
        rl_out = engine.infer(
            state_vec,
            prior_probs={"BUY": 0.40, "SELL": 0.30, "HOLD": 0.30},
            module_reliability={"cv_quality": 0.85, "structure_consistency": 0.82, "sequence_clarity": 0.74, "memory_novelty": 0.18},
        )
        engine.record_inference_context(
            image_hash="img_feedback",
            state_vec=state_vec,
            prior_probs={"BUY": 0.40, "SELL": 0.30, "HOLD": 0.30},
            policy_result=rl_out,
            predicted_action="BUY",
            memory_recall_top1_sim=0.90,
            memory_recall_direction="BUY",
            module_reliability={"cv_quality": 0.85},
        )

        feedback_image = root / "feedback_result.png"
        feedback_image.write_bytes(b"feedback-image")
        feedback = engine.record_feedback(
            "img_feedback",
            "BUY",
            "clean continuation",
            feedback_image_path=str(feedback_image),
            feedback_image_sha256="feedback_sha256",
            feedback_image_meta={"width": 640, "height": 360},
        )

        assert str(feedback["feedback_image_path"]) == str(feedback_image)
        assert str(feedback["feedback_image_sha256"]) == "feedback_sha256"
        assert int(feedback["feedback_image_meta"]["width"]) == 640

        rows = [json.loads(line) for line in (root / "rl_feedback.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
        assert rows[-1]["feedback_image_path"] == str(feedback_image)


def test_rl_feedback_update_leaves_no_temp_files() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        engine = RLPolicyEngine(
            logger=_NullLogger(),
            in_dim=64,
            mcts_sims=5,
            state_path=root / "rl_state.pt",
            feedback_buffer_path=root / "rl_feedback.jsonl",
            pending_contexts_path=root / "rl_pending.json",
        )

        for idx in range(4):
            state_vec = np.full((64,), fill_value=float(idx + 1) / 20.0, dtype=np.float32)
            rl_out = engine.infer(
                state_vec,
                prior_probs={"BUY": 0.34, "SELL": 0.33, "HOLD": 0.33},
                module_reliability={"cv_quality": 0.8, "structure_consistency": 0.75, "sequence_clarity": 0.7, "memory_novelty": 0.2},
            )
            engine.record_inference_context(
                image_hash=f"img_atomic_{idx}",
                state_vec=state_vec,
                prior_probs={"BUY": 0.34, "SELL": 0.33, "HOLD": 0.33},
                policy_result=rl_out,
                predicted_action="BUY",
                memory_recall_top1_sim=0.90,
                memory_recall_direction="BUY",
                module_reliability={"cv_quality": 0.8},
            )
            engine.record_feedback(f"img_atomic_{idx}", "BUY", "clean continuation")

        assert not list(root.glob("*.tmp"))


def test_rl_feedback_update_retries_transient_policy_replace_contention(monkeypatch: Any) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        engine = RLPolicyEngine(
            logger=_NullLogger(),
            in_dim=64,
            mcts_sims=5,
            state_path=root / "rl_state.pt",
            feedback_buffer_path=root / "rl_feedback.jsonl",
            pending_contexts_path=root / "rl_pending.json",
        )

        original_replace = Path.replace
        calls = {"count": 0}

        def _flaky_replace(self: Path, target: str | Path) -> Path:
            if self.parent == root and self.name.startswith("rl_state.pt.") and calls["count"] < 2:
                calls["count"] += 1
                raise PermissionError(32, "The process cannot access the file because it is being used by another process")
            return original_replace(self, target)

        monkeypatch.setattr(Path, "replace", _flaky_replace)

        feedback: dict[str, Any] | None = None
        for idx in range(4):
            state_vec = np.full((64,), fill_value=float(idx + 1) / 30.0, dtype=np.float32)
            rl_out = engine.infer(
                state_vec,
                prior_probs={"BUY": 0.34, "SELL": 0.33, "HOLD": 0.33},
                module_reliability={"cv_quality": 0.8, "structure_consistency": 0.75, "sequence_clarity": 0.7, "memory_novelty": 0.2},
            )
            engine.record_inference_context(
                image_hash=f"img_retry_{idx}",
                state_vec=state_vec,
                prior_probs={"BUY": 0.34, "SELL": 0.33, "HOLD": 0.33},
                policy_result=rl_out,
                predicted_action="BUY",
                memory_recall_top1_sim=0.90,
                memory_recall_direction="BUY",
                module_reliability={"cv_quality": 0.8},
            )
            feedback = engine.record_feedback(f"img_retry_{idx}", "BUY", "clean continuation")

        assert feedback is not None
        assert cast(bool, feedback["updated"]) is True
        assert (root / "rl_state.pt").exists()
        assert calls["count"] == 2
