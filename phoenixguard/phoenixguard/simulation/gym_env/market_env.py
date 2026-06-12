from __future__ import annotations

import copy
import random
from typing import Any, Mapping, Sequence

from phoenixguard.simulation.synthetic_scenarios.generator import generate_synthetic_market_suite


ACTION_HOLD = 0
ACTION_BUY = 1
ACTION_SELL = 2

ACTION_NAMES: dict[int, str] = {
    ACTION_HOLD: "HOLD",
    ACTION_BUY: "BUY",
    ACTION_SELL: "SELL",
}

ACTION_IDS: dict[str, int] = {name: action_id for action_id, name in ACTION_NAMES.items()}


class OfflineDiscreteActionSpace:
    """Small Gymnasium-like discrete action space without importing Gymnasium."""

    def __init__(self, n: int, *, seed: int | None = None) -> None:
        self.n = int(n)
        self._rng = random.Random(seed)

    def sample(self) -> int:
        return self._rng.randrange(self.n)

    def seed(self, seed: int | None = None) -> list[int | None]:
        self._rng.seed(seed)
        return [seed]

    def contains(self, value: object) -> bool:
        return isinstance(value, int) and 0 <= value < self.n


class OfflineDictObservationSpace:
    """Minimal observation-space validator for PhoenixGuardMarketEnv observations."""

    def contains(self, value: object) -> bool:
        return (
            isinstance(value, Mapping)
            and isinstance(value.get("frame"), Mapping)
            and isinstance(value.get("ohlc"), Mapping)
            and isinstance(value.get("features"), Mapping)
            and "frame_index" in value
        )


class PhoenixGuardMarketEnv:
    """
    Offline market replay environment with a Gymnasium-like reset/step API.

    The environment never places, prepares, or delegates real broker actions.
    Actions are scored only against offline scenario labels.
    """

    metadata = {
        "name": "PhoenixGuardMarketEnv",
        "render_modes": ("ansi", "human"),
        "offline_only": True,
        "broker_actions_allowed": False,
    }

    def __init__(
        self,
        scenarios: Sequence[Mapping[str, Any]] | None = None,
        *,
        seed: int = 0,
        max_steps: int | None = None,
    ) -> None:
        source = scenarios if scenarios is not None else generate_synthetic_market_suite(seed=seed)
        self.scenarios = [copy.deepcopy(dict(scenario)) for scenario in source]
        if not self.scenarios:
            raise ValueError("PhoenixGuardMarketEnv requires at least one offline scenario")

        for scenario in self.scenarios:
            self._validate_scenario(scenario)

        self.action_space = OfflineDiscreteActionSpace(len(ACTION_NAMES), seed=seed)
        self.observation_space = OfflineDictObservationSpace()
        self._base_max_steps = max_steps
        self._scenario_index = 0
        self._cursor = 0
        self._step_count = 0
        self._max_steps = 0
        self._done = True

    @property
    def current_scenario(self) -> dict[str, Any]:
        return self.scenarios[self._scenario_index]

    def reset(
        self,
        *,
        seed: int | None = None,
        options: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        options_dict = dict(options or {})
        self._scenario_index = self._select_scenario_index(seed=seed, options=options_dict)
        scenario = self.current_scenario
        self._cursor = 0
        self._step_count = 0
        scenario_frame_count = len(scenario["frames"])
        option_max_steps = options_dict.get("max_steps")
        chosen_max = int(option_max_steps) if option_max_steps is not None else self._base_max_steps
        self._max_steps = min(int(chosen_max), scenario_frame_count) if chosen_max else scenario_frame_count
        self._done = False
        observation = self._observation(0)
        info = self._info_for(action_name=None, reward=0.0)
        return observation, info

    def step(self, action: int | str) -> tuple[dict[str, Any], float, bool, bool, dict[str, Any]]:
        if self._done:
            raise RuntimeError("PhoenixGuardMarketEnv.step() called after termination; call reset() first")

        action_name = normalize_action(action)
        scenario = self.current_scenario
        labels = scenario["labels"]
        current_label = labels[self._cursor]
        reward = _reward_for(action_name, current_label, scenario.get("expected", {}))

        self._cursor += 1
        self._step_count += 1
        terminated = self._cursor >= len(scenario["frames"])
        truncated = self._step_count >= self._max_steps and not terminated
        self._done = terminated or truncated

        observation_index = min(self._cursor, len(scenario["frames"]) - 1)
        observation = self._observation(
            observation_index,
            terminal=terminated,
            truncated=truncated,
        )
        info = self._info_for(
            action_name=action_name,
            reward=reward,
            label=current_label,
            terminated=terminated,
            truncated=truncated,
        )
        return observation, reward, terminated, truncated, info

    def render(self, mode: str = "ansi") -> str | None:
        scenario = self.current_scenario
        frame_index = min(self._cursor, len(scenario["frames"]) - 1)
        frame = scenario["frames"][frame_index]
        close = frame["ohlc"]["close"]
        text = (
            f"{scenario['scenario_id']} frame={frame_index} "
            f"category={scenario['category']} close={close}"
        )
        if mode == "human":
            print(text)
            return None
        if mode == "ansi":
            return text
        raise ValueError("render mode must be 'ansi' or 'human'")

    def close(self) -> None:
        self._done = True

    def _select_scenario_index(self, *, seed: int | None, options: Mapping[str, Any]) -> int:
        scenario_id = options.get("scenario_id")
        category = options.get("category")
        if scenario_id is not None:
            return self._find_scenario_index("scenario_id", str(scenario_id))
        if category is not None:
            return self._find_scenario_index("category", str(category))
        if seed is not None:
            return int(seed) % len(self.scenarios)
        return 0

    def _find_scenario_index(self, field: str, value: str) -> int:
        normalized = value.strip().lower()
        for index, scenario in enumerate(self.scenarios):
            if str(scenario.get(field, "")).strip().lower() == normalized:
                return index
        raise ValueError(f"offline scenario with {field}={value!r} was not found")

    def _observation(
        self,
        frame_index: int,
        *,
        terminal: bool = False,
        truncated: bool = False,
    ) -> dict[str, Any]:
        scenario = self.current_scenario
        frame = copy.deepcopy(scenario["frames"][frame_index])
        return {
            "scenario_id": scenario["scenario_id"],
            "category": scenario["category"],
            "frame_index": int(frame_index),
            "frame": frame,
            "ohlc": copy.deepcopy(frame["ohlc"]),
            "features": copy.deepcopy(frame["features"]),
            "terminal": bool(terminal),
            "truncated": bool(truncated),
            "offline_only": True,
            "broker_actions_allowed": False,
        }

    def _info_for(
        self,
        *,
        action_name: str | None,
        reward: float,
        label: Mapping[str, Any] | None = None,
        terminated: bool = False,
        truncated: bool = False,
    ) -> dict[str, Any]:
        scenario = self.current_scenario
        active_index = min(self._cursor, len(scenario["frames"]) - 1)
        return {
            "scenario_id": scenario["scenario_id"],
            "category": scenario["category"],
            "frame_index": int(active_index),
            "action_name": action_name,
            "reward": float(reward),
            "label": copy.deepcopy(dict(label)) if label is not None else None,
            "expected": copy.deepcopy(scenario.get("expected", {})),
            "terminated": bool(terminated),
            "truncated": bool(truncated),
            "offline_only": True,
            "broker_action": None,
            "broker_actions_allowed": False,
            "simulated_action_only": True,
        }

    @staticmethod
    def _validate_scenario(scenario: Mapping[str, Any]) -> None:
        frames = scenario.get("frames")
        labels = scenario.get("labels")
        if not isinstance(frames, list) or not frames:
            raise ValueError("offline scenario must contain a non-empty frames list")
        if not isinstance(labels, list) or len(labels) != len(frames):
            raise ValueError("offline scenario labels must be a list aligned one-to-one with frames")
        for frame in frames:
            if not isinstance(frame, Mapping) or "ohlc" not in frame or "features" not in frame:
                raise ValueError("each offline scenario frame must contain ohlc and features dictionaries")


def normalize_action(action: int | str) -> str:
    if isinstance(action, str):
        action_name = action.strip().upper()
        if action_name not in ACTION_IDS:
            allowed = ", ".join(ACTION_IDS)
            raise ValueError(f"unknown action {action!r}; expected one of: {allowed}")
        return action_name
    if isinstance(action, int) and action in ACTION_NAMES:
        return ACTION_NAMES[action]
    raise ValueError(f"unknown action {action!r}; expected 0/HOLD, 1/BUY, or 2/SELL")


def _reward_for(action_name: str, label: Mapping[str, Any], expected: Mapping[str, Any]) -> float:
    target = str(label.get("target_action", "HOLD")).upper()
    safe_actions = {str(action).upper() for action in label.get("safe_actions", [])}
    if not safe_actions:
        safe_actions = {str(action).upper() for action in expected.get("safe_actions", [])}
    if not safe_actions:
        safe_actions = {target}

    trade_allowed = bool(label.get("trade_allowed", target != "HOLD"))
    if not trade_allowed and action_name in {"BUY", "SELL"}:
        return -1.0
    if action_name == target:
        return 1.0 if target != "HOLD" else 0.25
    if action_name in safe_actions:
        return 0.1
    if action_name == "HOLD" and target in {"BUY", "SELL"}:
        return -0.1
    return -0.5


__all__ = [
    "ACTION_BUY",
    "ACTION_HOLD",
    "ACTION_IDS",
    "ACTION_NAMES",
    "ACTION_SELL",
    "OfflineDictObservationSpace",
    "OfflineDiscreteActionSpace",
    "PhoenixGuardMarketEnv",
    "normalize_action",
]
