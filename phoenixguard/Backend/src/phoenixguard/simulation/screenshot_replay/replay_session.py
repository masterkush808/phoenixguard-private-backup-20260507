from __future__ import annotations

from dataclasses import dataclass, field
import time
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence, cast

from phoenixguard.decision.model_council_v3 import ModelCouncilV3

from .replay_clock import ReplayClock, ReplayMode, ReplaySpeedController, resolve_replay_mode
from .replay_loader import ReplayFrame, ReplayLoader
from .replay_metrics import ReplayMetricsRecorder
from .replay_packet_publisher import ReplayPacket, ReplayPacketPublisher


CouncilEvaluator = Callable[[Mapping[str, Any]], Mapping[str, Any]]


def _empty_frames() -> list[ReplayFrame]:
    return []


def _call_paper_executor(
    executor: object | None,
    packet: ReplayPacket,
    council_result: Mapping[str, Any],
) -> dict[str, Any] | None:
    if executor is None:
        return None
    record_executable = getattr(executor, "record_executable_packet", None)
    if callable(record_executable):
        result = cast(Callable[[dict[str, Any]], object], record_executable)(dict(council_result))
        return dict(cast(Mapping[str, Any], result)) if isinstance(result, Mapping) else None
    for method_name in ("on_council_result", "record_decision", "execute"):
        method = getattr(executor, method_name, None)
        if callable(method):
            result = cast(Callable[[dict[str, Any], dict[str, Any]], object], method)(
                packet.as_dict(),
                dict(council_result),
            )
            return dict(cast(Mapping[str, Any], result)) if isinstance(result, Mapping) else None
    if callable(executor):
        result = cast(Callable[[dict[str, Any], dict[str, Any]], object], executor)(
            packet.as_dict(),
            dict(council_result),
        )
        return dict(cast(Mapping[str, Any], result)) if isinstance(result, Mapping) else None
    return None


@dataclass(frozen=True)
class ReplaySessionConfig:
    replay_root: Path
    mode: ReplayMode = ReplayMode.FAST_REPLAY
    simulation_id: str = "sim-local"
    scenario_name: str = ""
    limit: int | None = None
    sleep_enabled: bool = False
    speed_multiplier: float = 1.0


@dataclass
class ReplaySession:
    config: ReplaySessionConfig
    loader: ReplayLoader | None = None
    publisher: ReplayPacketPublisher = field(default_factory=ReplayPacketPublisher)
    council_evaluator: CouncilEvaluator | None = None
    paper_executor: object | None = None
    metrics: ReplayMetricsRecorder | None = None
    clock: ReplayClock | None = None
    frames: list[ReplayFrame] = field(default_factory=_empty_frames)
    cursor: int = 0

    @classmethod
    def from_root(
        cls,
        replay_root: str | Path,
        *,
        mode: ReplayMode | str = ReplayMode.FAST_REPLAY,
        simulation_id: str = "sim-local",
        scenario_name: str = "",
        limit: int | None = None,
        sleep_enabled: bool = False,
        speed_multiplier: float = 1.0,
        loader: ReplayLoader | None = None,
        publisher: ReplayPacketPublisher | None = None,
        council_evaluator: CouncilEvaluator | None = None,
        paper_executor: object | None = None,
        metrics: ReplayMetricsRecorder | None = None,
        clock: ReplayClock | None = None,
        frames: Sequence[ReplayFrame] | None = None,
        cursor: int = 0,
    ) -> "ReplaySession":
        resolved_mode = resolve_replay_mode(mode)
        config = ReplaySessionConfig(
            replay_root=Path(replay_root),
            mode=resolved_mode,
            simulation_id=simulation_id,
            scenario_name=scenario_name,
            limit=limit,
            sleep_enabled=bool(sleep_enabled),
            speed_multiplier=float(speed_multiplier),
        )
        return cls(
            config=config,
            loader=loader,
            publisher=publisher or ReplayPacketPublisher(),
            council_evaluator=council_evaluator,
            paper_executor=paper_executor,
            metrics=metrics,
            clock=clock,
            frames=list(frames or []),
            cursor=int(cursor),
        )

    def prepare(self) -> None:
        self.loader = self.loader or ReplayLoader(self.config.replay_root)
        self.frames = self.loader.load(limit=self.config.limit)
        self.metrics = self.metrics or ReplayMetricsRecorder(
            simulation_id=self.config.simulation_id,
            scenario_name=self.config.scenario_name or self.config.replay_root.name,
        )
        self.clock = self.clock or ReplayClock(
            speed=ReplaySpeedController(mode=self.config.mode, speed_multiplier=self.config.speed_multiplier),
            sleep_enabled=self.config.sleep_enabled,
        )
        if self.council_evaluator is None:
            council = ModelCouncilV3()
            self.council_evaluator = lambda snapshot: council.evaluate(snapshot, now_epoch=time.time())

    def step(self) -> dict[str, Any] | None:
        if not self.frames:
            self.prepare()
        if self.cursor >= len(self.frames):
            return None
        assert self.clock is not None
        assert self.metrics is not None
        assert self.council_evaluator is not None
        frame = self.frames[self.cursor]
        self.cursor += 1
        clock_report = self.clock.advance(frame.as_dict())
        started = time.perf_counter()
        packet = self.publisher.publish(frame)
        council_result = dict(self.council_evaluator(packet.snapshot))
        latency_ms = (time.perf_counter() - started) * 1000.0
        paper_result = _call_paper_executor(self.paper_executor, packet, council_result)
        self.metrics.record_frame(
            packet=packet.as_dict(),
            council_result=council_result,
            paper_result=paper_result,
            latency_ms=latency_ms,
        )
        return {
            "clock": clock_report,
            "packet": packet.as_dict(),
            "council_result": council_result,
            "paper_result": paper_result,
            "latency_ms": round(float(latency_ms), 3),
        }

    def run(self) -> dict[str, Any]:
        self.prepare()
        rows: list[dict[str, Any]] = []
        while True:
            row = self.step()
            if row is None:
                break
            rows.append(row)
            if self.config.mode == ReplayMode.STEP_FRAME_BY_FRAME:
                break
        assert self.metrics is not None
        return {"frames": rows, "summary": self.metrics.summary()}
