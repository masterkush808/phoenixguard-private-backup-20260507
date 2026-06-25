"""
PhoenixGuard SIGE-VLA 3.0 - Reinforcement Learning Policy Engine
================================================================
Skills wired:
  - Reinforcement Learning (persistent residual policy head)
  - Monte Carlo Tree Search (noise simulation tree for action exploration)
  - Transfer Learning (verified online updates anchored to prior probabilities)
  - Reward Shaping (direction match + candle count + memory match + sharpe proxy)
"""
from __future__ import annotations

from dataclasses import dataclass, field
import importlib
import json
import os
from pathlib import Path
from threading import RLock
import tempfile
import time
from typing import Any, Mapping, cast

import numpy as np
from numpy.typing import NDArray
import torch
import torch.nn as nn
import torch.nn.functional as F

from phoenixguard.core.config import RUNTIME, TRAIN


ACTIONS = ["BUY", "SELL", "HOLD"]
_MEMORY_BOOST_THRESHOLD = 0.85
_MEMORY_BOOST_VAL = 0.25
_ONLINE_UPDATE_EVERY = 50


def _clip01(value: Any, default: float = 0.0) -> float:
    try:
        return float(np.clip(float(value), 0.0, 1.0))
    except Exception:
        return float(default)


def _safe_action(value: Any, default: str = "HOLD") -> str:
    text = str(value or "").strip().upper()
    return text if text in ACTIONS else default


def _safe_probs(probs: Mapping[str, Any] | None) -> NDArray[np.float32]:
    if probs is None:
        return np.asarray([1.0 / 3.0, 1.0 / 3.0, 1.0 / 3.0], dtype=np.float32)
    vec = np.asarray(
        [
            max(0.0, float(probs.get("BUY", 0.0) or 0.0)),
            max(0.0, float(probs.get("SELL", 0.0) or 0.0)),
            max(0.0, float(probs.get("HOLD", 0.0) or 0.0)),
        ],
        dtype=np.float32,
    )
    total = float(vec.sum())
    if total <= 1e-8:
        vec[:] = np.float32(1.0 / 3.0)
        return vec
    vec = np.clip(vec / np.float32(total), 1e-6, 1.0)
    return cast(NDArray[np.float32], vec / np.float32(max(float(vec.sum()), 1e-8)))


def _probs_to_dict(vec: NDArray[np.float32]) -> dict[str, float]:
    return {
        "BUY": float(vec[0]),
        "SELL": float(vec[1]),
        "HOLD": float(vec[2]),
    }


def _empty_probability_map() -> dict[str, float]:
    return {}


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    _atomic_write_bytes(
        path,
        json.dumps(payload, ensure_ascii=True, indent=2, sort_keys=True).encode("utf-8"),
    )


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=str(path.parent),
            prefix=f"{path.name}.",
            suffix=".tmp",
        ) as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            tmp_path = Path(handle.name)
        for attempt in range(6):
            try:
                tmp_path.replace(path)
                break
            except PermissionError:
                if attempt >= 5:
                    raise
                time.sleep(0.05 * float(attempt + 1))
    finally:
        if tmp_path is not None and tmp_path.exists():
            try:
                tmp_path.unlink()
            except Exception:
                pass


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if isinstance(row, dict):
                    rows.append(cast(dict[str, Any], row))
    except Exception:
        return []
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    payload = "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in rows)
    _atomic_write_bytes(path, payload.encode("utf-8"))


def _find_feedback_by_submission_id(
    rows: list[dict[str, Any]],
    submission_id: str,
) -> dict[str, Any] | None:
    target = str(submission_id or "").strip()
    if not target:
        return None
    for row in reversed(rows):
        if str(row.get("submission_id", "")).strip() == target:
            return dict(row)
    return None


@dataclass
class RLResult:
    probs: dict[str, float]
    mcts_value: float
    boosted_action: str = "HOLD"
    boost_applied: bool = False
    blend_weight: float = 1.0
    prior_probs: dict[str, float] = field(default_factory=_empty_probability_map)
    policy_probs: dict[str, float] = field(default_factory=_empty_probability_map)
    policy_action: str = "HOLD"
    feedback_count: int = 0
    online_update_count: int = 0
    contribution_gate_open: bool = False
    contribution_score: float = 0.0
    rolling_accuracy: float = 0.0
    baseline_accuracy: float = 0.0
    accuracy_improvement: float = 0.0
    contribution_gate_reason: str = "insufficient_verified_feedback"


class GRPOPolicyHead(nn.Module):
    def __init__(self, in_dim: int = 64, hidden: int = 64, n_actions: int = 3):
        super().__init__()
        self.net = nn.Sequential(
            nn.Linear(in_dim, hidden),
            nn.GELU(),
            nn.Linear(hidden, n_actions),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


class RLPolicyEngine:
    def __init__(
        self,
        logger: Any,
        in_dim: int = 64,
        mcts_sims: int = 20,
        *,
        state_path: str | Path | None = None,
        feedback_buffer_path: str | Path | None = None,
        pending_contexts_path: str | Path | None = None,
    ) -> None:
        self.logger = logger
        self._lock = RLock()
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = GRPOPolicyHead(in_dim=in_dim).to(self.device)
        self.mcts_sims = int(max(mcts_sims, 1))
        self.lr = float(max(TRAIN.rl_learning_rate, 1e-5))
        self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=self.lr, weight_decay=1e-4)
        self.state_path = Path(state_path) if state_path is not None else Path(RUNTIME.rl_policy_state_path)
        self.feedback_buffer_path = Path(feedback_buffer_path) if feedback_buffer_path is not None else Path(RUNTIME.rl_feedback_buffer_path)
        self.pending_contexts_path = Path(pending_contexts_path) if pending_contexts_path is not None else Path(RUNTIME.rl_pending_contexts_path)
        self._recall_counter = 0
        self._feedback_count = 0
        self._online_update_count = 0
        self._last_loss = 0.0
        self._feedback_buffer: list[dict[str, Any]] = _load_jsonl(self.feedback_buffer_path)[-int(max(TRAIN.rl_replay_window, 1)) :]
        self._pending_contexts = cast(dict[str, dict[str, Any]], _read_json(self.pending_contexts_path, {}))

        self.grpo_available = False
        try:
            trl = importlib.import_module("trl")
            self.grpo_available = getattr(trl, "GRPOTrainer", None) is not None
        except Exception:
            pass

        self._load_state()

    def _load_state(self) -> None:
        if not self.state_path.exists():
            self._feedback_count = len(self._feedback_buffer)
            return
        try:
            try:
                payload: Any = torch.load(self.state_path, map_location=self.device, weights_only=False)
            except TypeError:
                payload = torch.load(self.state_path, map_location=self.device)
            if not isinstance(payload, Mapping):
                return
            payload_map = cast(Mapping[str, Any], payload)
            model_state = payload_map.get("model_state_dict")
            optimizer_state = payload_map.get("optimizer_state_dict")
            if isinstance(model_state, Mapping):
                self.model.load_state_dict(cast(Any, model_state))
            if isinstance(optimizer_state, Mapping):
                self.optimizer.load_state_dict(cast(Any, optimizer_state))
            self._recall_counter = int(payload_map.get("recall_counter", 0) or 0)
            self._feedback_count = int(payload_map.get("feedback_count", len(self._feedback_buffer)) or len(self._feedback_buffer))
            self._online_update_count = int(payload_map.get("online_update_count", 0) or 0)
            self._last_loss = float(payload_map.get("last_loss", 0.0) or 0.0)
        except Exception as exc:
            self.logger.warning("RL policy state load failed: %s", exc)
            self._feedback_count = len(self._feedback_buffer)

    def _save_state(self) -> None:
        payload: dict[str, Any] = {
            "model_state_dict": self.model.state_dict(),
            "optimizer_state_dict": self.optimizer.state_dict(),
            "recall_counter": int(self._recall_counter),
            "feedback_count": int(self._feedback_count),
            "online_update_count": int(self._online_update_count),
            "last_loss": float(self._last_loss),
        }
        tmp_path: Path | None = None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                delete=False,
                dir=str(self.state_path.parent),
                prefix=f"{self.state_path.name}.",
                suffix=".tmp",
            ) as handle:
                tmp_path = Path(handle.name)
            torch.save(payload, tmp_path)
            for attempt in range(6):
                try:
                    tmp_path.replace(self.state_path)
                    break
                except PermissionError:
                    if attempt >= 5:
                        raise
                    time.sleep(0.05 * float(attempt + 1))
        finally:
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception:
                    pass

    def _save_pending_contexts(self) -> None:
        _write_json(self.pending_contexts_path, self._pending_contexts)

    def _save_feedback_buffer(self) -> None:
        _write_jsonl(self.feedback_buffer_path, self._feedback_buffer[-int(max(TRAIN.rl_replay_window, 1)) :])

    @staticmethod
    def _normalized_entropy(probabilities: NDArray[np.float32]) -> float:
        vec = np.clip(probabilities.astype(np.float64), 1e-8, 1.0)
        vec /= max(float(vec.sum()), 1e-12)
        entropy = -float(np.sum(vec * np.log(vec)))
        return float(entropy / np.log(3.0))

    def _policy_blend_weight(
        self,
        *,
        has_prior: bool,
        module_reliability: Mapping[str, Any] | None = None,
        contribution_gate_open: bool = False,
    ) -> float:
        if not has_prior:
            return 1.0
        if not contribution_gate_open:
            return 0.0
        min_feedback = int(max(TRAIN.rl_min_feedback_before_blend, 0))
        if self._feedback_count < min_feedback:
            return 0.0
        rel = module_reliability or {}
        cv_quality = _clip01(rel.get("cv_quality", 0.5), 0.5)
        structure_consistency = _clip01(rel.get("structure_consistency", cv_quality), cv_quality)
        sequence_clarity = _clip01(rel.get("sequence_clarity", structure_consistency), structure_consistency)
        memory_novelty = _clip01(rel.get("memory_novelty", 0.5), 0.5)
        reliability = float(
            np.clip(
                0.38 * cv_quality
                + 0.32 * structure_consistency
                + 0.18 * sequence_clarity
                + 0.12 * (1.0 - memory_novelty),
                0.0,
                1.0,
            )
        )
        progress = float(
            np.clip(
                (self._feedback_count - min_feedback + 1) / max(3 * max(min_feedback, 1), 1),
                0.0,
                1.0,
            )
        )
        blend_cap = float(np.clip(TRAIN.rl_policy_blend_cap, 0.0, 0.45))
        return float(np.clip(blend_cap * (0.30 + 0.70 * progress) * (0.55 + 0.45 * reliability), 0.0, blend_cap))

    def _contribution_gate_snapshot(self) -> dict[str, Any]:
        min_feedback = int(max(TRAIN.rl_min_feedback_before_blend, 4))
        window = min(max(int(TRAIN.rl_feedback_batch_size), min_feedback), max(len(self._feedback_buffer), min_feedback))
        recent = list(self._feedback_buffer[-window:]) if self._feedback_buffer else []
        sample_count = len(recent)
        if sample_count < min_feedback:
            return {
                "open": False,
                "sample_count": sample_count,
                "required_accuracy": 0.80,
                "rolling_accuracy": 0.0,
                "baseline_accuracy": 0.0,
                "accuracy_improvement": 0.0,
                "contribution_score": 0.0,
                "reason": f"waiting_for_{min_feedback}_verified_feedback_items",
            }

        policy_correct = 0
        baseline_correct = 0
        evaluated = 0
        for row in recent:
            target = _safe_action(row.get("target_action", "HOLD"))
            if target not in ACTIONS:
                continue
            evaluated += 1
            if _safe_action(row.get("policy_action", "HOLD")) == target:
                policy_correct += 1
            prior_probs = _safe_probs(cast(Mapping[str, Any] | None, row.get("prior_probs")))
            if ACTIONS[int(np.argmax(prior_probs))] == target:
                baseline_correct += 1
        if evaluated <= 0:
            return {
                "open": False,
                "sample_count": sample_count,
                "required_accuracy": 0.80,
                "rolling_accuracy": 0.0,
                "baseline_accuracy": 0.0,
                "accuracy_improvement": 0.0,
                "contribution_score": 0.0,
                "reason": "no_directional_feedback_available",
            }
        rolling_accuracy = float(policy_correct / evaluated)
        baseline_accuracy = float(baseline_correct / evaluated)
        lift = max(0.0, rolling_accuracy - baseline_accuracy)
        gate_open = bool(rolling_accuracy >= 0.80 and rolling_accuracy >= baseline_accuracy)
        return {
            "open": gate_open,
            "sample_count": sample_count,
            "required_accuracy": 0.80,
            "rolling_accuracy": rolling_accuracy,
            "baseline_accuracy": baseline_accuracy,
            "accuracy_improvement": lift,
            "contribution_score": rolling_accuracy,
            "reason": (
                f"open_accuracy_{rolling_accuracy:.2f}"
                if gate_open
                else f"accuracy_{rolling_accuracy:.2f}_below_0.80_or_below_baseline_{baseline_accuracy:.2f}"
            ),
        }

    def _run_mcts(self, probabilities: NDArray[np.float32]) -> float:
        sim_values: list[float] = []
        for _ in range(self.mcts_sims):
            noise = np.random.normal(0.0, 0.02, size=probabilities.shape).astype(np.float32)
            noisy = np.clip(probabilities + noise, 1e-6, 1.0).astype(np.float32)
            noisy = noisy / np.float32(max(float(noisy.sum()), 1e-8))
            sim_values.append(float(noisy.max() - noisy.min()))
        return float(np.mean(sim_values)) if sim_values else 0.0

    @torch.inference_mode()
    def infer(
        self,
        fused_vec: NDArray[np.float32],
        memory_recall_top1_sim: float = 0.0,
        memory_recall_direction: str = "HOLD",
        *,
        prior_probs: Mapping[str, Any] | None = None,
        module_reliability: Mapping[str, Any] | None = None,
    ) -> RLResult:
        with self._lock:
            x = torch.tensor(fused_vec, dtype=torch.float32, device=self.device).unsqueeze(0)
            logits = self.model(x).squeeze(0)
            policy_tensor: Any = torch.softmax(logits, dim=-1).detach().cpu().to(torch.float32)
            policy_probs = np.asarray(policy_tensor.numpy(), dtype=np.float32)

            prior_vec = _safe_probs(prior_probs) if prior_probs is not None else policy_probs.copy()
            contribution_gate = self._contribution_gate_snapshot()
            blend_weight = self._policy_blend_weight(
                has_prior=prior_probs is not None,
                module_reliability=module_reliability,
                contribution_gate_open=bool(contribution_gate.get("open", False)),
            )
            final_probs = policy_probs.copy()
            if prior_probs is not None:
                final_probs = (
                    (np.float32(1.0) - np.float32(blend_weight)) * prior_vec
                    + np.float32(blend_weight) * policy_probs
                ).astype(np.float32)
                final_probs = np.clip(final_probs, 1e-6, 1.0)
                final_probs = final_probs / np.float32(max(float(final_probs.sum()), 1e-8))

            boosted_action = ACTIONS[int(np.argmax(final_probs))]
            boost_applied = False
            if (
                (prior_probs is None or bool(contribution_gate.get("open", False)))
                and float(memory_recall_top1_sim) >= _MEMORY_BOOST_THRESHOLD
                and _safe_action(memory_recall_direction) in {"BUY", "SELL"}
            ):
                boosted_logits = np.log(np.clip(final_probs.astype(np.float64), 1e-8, 1.0))
                boost_index = ACTIONS.index(_safe_action(memory_recall_direction))
                boosted_logits[boost_index] += float(_MEMORY_BOOST_VAL)
                boosted_tensor: Any = torch.softmax(
                    torch.tensor(boosted_logits, dtype=torch.float32),
                    dim=-1,
                ).detach().cpu()
                final_probs = np.asarray(boosted_tensor.numpy(), dtype=np.float32)
                boost_applied = True
                boosted_action = _safe_action(memory_recall_direction)
                self.logger.info(
                    "Memory recall boost applied inside RL residual policy: dir=%s sim=%.3f",
                    boosted_action,
                    float(memory_recall_top1_sim),
                )

            mcts_value = self._run_mcts(final_probs)
            return RLResult(
                probs=_probs_to_dict(final_probs),
                mcts_value=mcts_value,
                boosted_action=boosted_action,
                boost_applied=boost_applied,
                blend_weight=float(blend_weight),
                prior_probs=_probs_to_dict(prior_vec),
                policy_probs=_probs_to_dict(policy_probs),
                policy_action=ACTIONS[int(np.argmax(policy_probs))],
                feedback_count=int(self._feedback_count),
                online_update_count=int(self._online_update_count),
                contribution_gate_open=bool(contribution_gate.get("open", False)),
                contribution_score=float(contribution_gate.get("contribution_score", 0.0) or 0.0),
                rolling_accuracy=float(contribution_gate.get("rolling_accuracy", 0.0) or 0.0),
                baseline_accuracy=float(contribution_gate.get("baseline_accuracy", 0.0) or 0.0),
                accuracy_improvement=float(contribution_gate.get("accuracy_improvement", 0.0) or 0.0),
                contribution_gate_reason=str(contribution_gate.get("reason", "insufficient_verified_feedback")),
            )

    def compute_reward(
        self,
        predicted_action: str,
        actual_outcome: str,
        candle_count_correct: bool = False,
        memory_match: bool = False,
        sharpe_proxy: float = 0.0,
    ) -> float:
        reward = 0.0
        if _safe_action(predicted_action) == _safe_action(actual_outcome):
            reward += float(TRAIN.reward_direction_match)
        if candle_count_correct:
            reward += float(TRAIN.reward_candle_count_correct)
        if memory_match:
            reward += float(TRAIN.reward_memory_recall)
        reward += float(np.clip(sharpe_proxy, -0.5, 0.5))
        return float(reward)

    def record_inference_context(
        self,
        *,
        image_hash: str,
        context_id: str = "",
        state_vec: NDArray[np.float32],
        prior_probs: Mapping[str, Any],
        policy_result: RLResult,
        predicted_action: str,
        memory_recall_top1_sim: float = 0.0,
        memory_recall_direction: str = "HOLD",
        module_reliability: Mapping[str, Any] | None = None,
        map_context: Mapping[str, Any] | None = None,
    ) -> None:
        image_key = str(image_hash)
        pending_key = str(context_id or image_key).strip() or image_key
        with self._lock:
            self._pending_contexts[pending_key] = {
                "context_id": pending_key,
                "image_hash": image_key,
                "state_vec": np.asarray(state_vec, dtype=np.float32).reshape(-1).astype(np.float32).tolist(),
                "prior_probs": dict(_probs_to_dict(_safe_probs(prior_probs))),
                "policy_probs": dict(policy_result.probs),
                "policy_action": str(policy_result.policy_action),
                "predicted_action": _safe_action(predicted_action),
                "blend_weight": float(policy_result.blend_weight),
                "memory_recall_top1_sim": float(np.clip(memory_recall_top1_sim, 0.0, 1.0)),
                "memory_recall_direction": _safe_action(memory_recall_direction),
                "module_reliability": dict(module_reliability or {}),
                "map_context": dict(map_context or {}),
                "contribution_gate_open": bool(policy_result.contribution_gate_open),
                "contribution_gate_reason": str(policy_result.contribution_gate_reason),
            }
            if len(self._pending_contexts) > 1024:
                keys = list(self._pending_contexts.keys())[-1024:]
                self._pending_contexts = {key: self._pending_contexts[key] for key in keys}
            self._save_pending_contexts()

    def _append_feedback_item(self, item: dict[str, Any]) -> None:
        with self._lock:
            self._feedback_buffer.append(item)
            self._feedback_buffer = self._feedback_buffer[-int(max(TRAIN.rl_replay_window, 1)) :]
            self._feedback_count = len(self._feedback_buffer)
            self._save_feedback_buffer()

    def append_feedback_item(self, item: dict[str, Any]) -> None:
        self._append_feedback_item(item)

    def _recent_feedback_batch(self) -> list[dict[str, Any]]:
        batch_size = int(max(TRAIN.rl_feedback_batch_size, 1))
        if not self._feedback_buffer:
            return []
        return list(self._feedback_buffer[-batch_size:])

    def _maybe_online_update_from_feedback(self) -> dict[str, Any]:
        with self._lock:
            min_feedback = int(max(TRAIN.rl_min_feedback_before_blend, 1))
            batch = self._recent_feedback_batch()
            if len(batch) < min_feedback:
                return {"updated": False, "loss": 0.0}
            loss = self.online_update(batch)
            self._online_update_count += 1
            self._last_loss = float(loss)
            self._save_state()
            return {"updated": True, "loss": float(loss)}

    def record_feedback(
        self,
        image_hash: str,
        actual_outcome: str,
        reason: str,
        *,
        context_id: str = "",
        submission_id: str = "",
        candle_count_correct: bool = False,
        sharpe_proxy: float = 0.0,
        operator_confidence: float = 1.0,
        feedback_meta: Mapping[str, Any] | None = None,
        feedback_image_path: str = "",
        feedback_image_sha256: str = "",
        feedback_image_meta: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        image_key = str(image_hash)
        pending_key = str(context_id or image_key).strip() or image_key
        with self._lock:
            if submission_id:
                existing = _find_feedback_by_submission_id(self._feedback_buffer, submission_id)
                if existing is not None:
                    return {
                        **existing,
                        "updated": False,
                        "loss": float(self._last_loss),
                        "feedback_count": int(self._feedback_count),
                        "online_update_count": int(self._online_update_count),
                    }
            context = dict(self._pending_contexts.get(pending_key, {}))
            if not context and pending_key != image_key:
                context = dict(self._pending_contexts.get(image_key, {}))
            if not context:
                return {}
            actual_action = _safe_action(actual_outcome)
            memory_direction = _safe_action(context.get("memory_recall_direction", "HOLD"))
            memory_match = (
                memory_direction == actual_action
                and float(context.get("memory_recall_top1_sim", 0.0) or 0.0) >= _MEMORY_BOOST_THRESHOLD
            )
            reward = float(TRAIN.reward_direction_match)
            if candle_count_correct:
                reward += float(TRAIN.reward_candle_count_correct)
            if memory_match:
                reward += float(TRAIN.reward_memory_recall)
            reward += float(np.clip(sharpe_proxy, -0.5, 0.5))
            if _safe_action(context.get("predicted_action", "HOLD")) == actual_action:
                reward += 0.10

            feedback_item: dict[str, Any] = {
                "submission_id": str(submission_id or "").strip(),
                "image_hash": image_key,
                "context_id": str(context.get("context_id", pending_key or image_key)),
                "state": cast(list[float], context.get("state_vec", [])),
                "target_action": actual_action,
                "reward": float(max(reward, 0.25)),
                "reason": str(reason),
                "prior_probs": dict(cast(dict[str, float], context.get("prior_probs", {}))),
                "policy_probs": dict(cast(dict[str, float], context.get("policy_probs", {}))),
                "predicted_action": _safe_action(context.get("predicted_action", "HOLD")),
                "policy_action": _safe_action(context.get("policy_action", "HOLD")),
                "memory_recall_top1_sim": float(context.get("memory_recall_top1_sim", 0.0) or 0.0),
                "memory_recall_direction": memory_direction,
                "blend_weight": float(context.get("blend_weight", 0.0) or 0.0),
                "memory_match": bool(memory_match),
                "candle_count_correct": bool(candle_count_correct),
                "sharpe_proxy": float(np.clip(sharpe_proxy, -0.5, 0.5)),
                "operator_confidence": float(np.clip(operator_confidence, 0.05, 1.0)),
                "feedback_meta": dict(feedback_meta or {}),
                "module_reliability": dict(cast(dict[str, Any], context.get("module_reliability", {}))),
                "map_context": dict(cast(dict[str, Any], context.get("map_context", {}))),
                "contribution_gate_open": bool(context.get("contribution_gate_open", False)),
                "contribution_gate_reason": str(context.get("contribution_gate_reason", "")),
                "feedback_image_path": str(feedback_image_path or "").strip(),
                "feedback_image_sha256": str(feedback_image_sha256 or "").strip(),
                "feedback_image_meta": dict(feedback_image_meta or {}),
            }
            self._feedback_buffer.append(feedback_item)
            self._feedback_buffer = self._feedback_buffer[-int(max(TRAIN.rl_replay_window, 1)) :]
            self._feedback_count = len(self._feedback_buffer)
            self._save_feedback_buffer()
            # Persist counters and optimizer state even before the first online update
            # so restart recovery resumes from the accumulated feedback window.
            self._save_state()
            update_summary = self._maybe_online_update_from_feedback()
            self._pending_contexts.pop(pending_key, None)
            if pending_key != image_key:
                legacy_context = self._pending_contexts.get(image_key)
                if isinstance(legacy_context, dict) and str(legacy_context.get("context_id", "")).strip() == pending_key:
                    self._pending_contexts.pop(image_key, None)
            self._save_pending_contexts()
            return {
                **feedback_item,
                **update_summary,
                "feedback_count": int(self._feedback_count),
                "online_update_count": int(self._online_update_count),
            }

    def record_recall_and_maybe_update(self, batch_item: dict[str, Any] | None = None) -> bool:
        with self._lock:
            self._recall_counter += 1
            if batch_item is not None and bool(batch_item.get("verified", False)):
                self._feedback_buffer.append(dict(batch_item))
                self._feedback_buffer = self._feedback_buffer[-int(max(TRAIN.rl_replay_window, 1)) :]
                self._feedback_count = len(self._feedback_buffer)
                self._save_feedback_buffer()

            if self._recall_counter % _ONLINE_UPDATE_EVERY == 0 and self._feedback_buffer:
                batch = self._recent_feedback_batch()
                min_feedback = int(max(TRAIN.rl_min_feedback_before_blend, 1))
                if len(batch) >= min_feedback:
                    self.logger.info(
                        "RL recall pacing trigger fired at %d recalls using %d verified feedback samples",
                        self._recall_counter,
                        len(batch),
                    )
                    self._last_loss = float(self.online_update(batch))
                    self._online_update_count += 1
                    self._save_state()
                    return True
            return False

    def online_update(self, batch: list[dict[str, Any]]) -> float:
        with self._lock:
            if not batch:
                return 0.0

            states = np.asarray([item.get("state", np.zeros((64,), dtype=np.float32)) for item in batch], dtype=np.float32)
            x = torch.tensor(states, dtype=torch.float32, device=self.device)
            y = torch.tensor(
                [ACTIONS.index(_safe_action(item.get("target_action", "HOLD"))) for item in batch],
                dtype=torch.long,
                device=self.device,
            )
            rewards = torch.tensor(
                [float(item.get("reward", 1.0) or 1.0) for item in batch],
                dtype=torch.float32,
                device=self.device,
            )
            operator_confidence = torch.tensor(
                [float(np.clip(item.get("operator_confidence", 1.0) or 1.0, 0.05, 1.0)) for item in batch],
                dtype=torch.float32,
                device=self.device,
            )
            prior_probs = torch.tensor(
                np.stack([_safe_probs(cast(Mapping[str, Any] | None, item.get("prior_probs"))) for item in batch], axis=0),
                dtype=torch.float32,
                device=self.device,
            )

            logits = self.model(x)
            log_probs = F.log_softmax(logits, dim=-1)
            probs = torch.exp(log_probs)
            ce_loss = F.cross_entropy(logits, y, reduction="none")
            reward_weights = (rewards * operator_confidence).clamp(min=0.15, max=3.0)
            supervised_loss = torch.mean(ce_loss * reward_weights)

            selected_log_probs = log_probs.gather(1, y.unsqueeze(1)).squeeze(1)
            advantages = rewards - rewards.mean()
            if float(torch.abs(advantages).mean().item()) < 1e-6:
                advantages = rewards - 1.0
            policy_loss = -torch.mean(advantages.detach() * selected_log_probs)

            prior_kl = F.kl_div(log_probs, prior_probs, reduction="batchmean")
            entropy = -torch.mean(torch.sum(probs * log_probs, dim=-1))
            loss = (
                0.60 * supervised_loss
                + 0.22 * policy_loss
                + float(TRAIN.rl_prior_kl_weight) * prior_kl
                - float(TRAIN.rl_entropy_bonus) * entropy
            )

            self.optimizer.zero_grad(set_to_none=True)
            cast(Any, loss).backward()
            nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
            cast(Any, self.optimizer).step()
            return float(loss.item())

    def stats_snapshot(self) -> dict[str, Any]:
        with self._lock:
            contribution_gate = self._contribution_gate_snapshot()
            return {
                "feedback_count": int(self._feedback_count),
                "online_update_count": int(self._online_update_count),
                "recall_counter": int(self._recall_counter),
                "last_loss": float(self._last_loss),
                "blend_cap": float(TRAIN.rl_policy_blend_cap),
                "min_feedback_before_blend": int(TRAIN.rl_min_feedback_before_blend),
                "contribution_gate_open": bool(contribution_gate.get("open", False)),
                "contribution_score": float(contribution_gate.get("contribution_score", 0.0) or 0.0),
                "rolling_accuracy": float(contribution_gate.get("rolling_accuracy", 0.0) or 0.0),
                "baseline_accuracy": float(contribution_gate.get("baseline_accuracy", 0.0) or 0.0),
                "accuracy_improvement": float(contribution_gate.get("accuracy_improvement", 0.0) or 0.0),
                "contribution_gate_reason": str(contribution_gate.get("reason", "")),
            }
