from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, cast


SUPPORTED_FRAME_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp")


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _empty_mapping() -> dict[str, Any]:
    return {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return _mapping(payload)


def _frame_number(path: Path, fallback: int) -> int:
    match = re.search(r"(\d+)$", path.stem)
    if not match:
        return int(fallback)
    return int(match.group(1))


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _lookup(table: Mapping[str, Any], frame_id: int, path: Path) -> dict[str, Any]:
    keys = (
        str(frame_id),
        f"{frame_id:06d}",
        path.name,
        path.stem,
        f"frame_{frame_id:06d}",
    )
    for key in keys:
        value = table.get(key)
        if isinstance(value, Mapping):
            return dict(cast(Mapping[str, Any], value))
    return {}


@dataclass(frozen=True)
class ReplayFrame:
    frame_id: int
    path: Path
    sequence_index: int
    timestamp: float = 0.0
    frame_interval_seconds: float = 0.0
    metadata: Mapping[str, Any] = field(default_factory=_empty_mapping)
    labels: Mapping[str, Any] = field(default_factory=_empty_mapping)
    expected: Mapping[str, Any] = field(default_factory=_empty_mapping)
    frame_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "frame_id": self.frame_id,
            "path": str(self.path),
            "sequence_index": self.sequence_index,
            "timestamp": self.timestamp,
            "frame_interval_seconds": self.frame_interval_seconds,
            "metadata": dict(self.metadata),
            "labels": dict(self.labels),
            "expected": dict(self.expected),
            "frame_hash": self.frame_hash,
        }


class ReplayLoader:
    def __init__(self, replay_root: str | Path) -> None:
        self.replay_root = Path(replay_root)

    @property
    def frames_dir(self) -> Path:
        nested = self.replay_root / "frames"
        return nested if nested.exists() else self.replay_root

    def load(self, *, limit: int | None = None) -> list[ReplayFrame]:
        frames_dir = self.frames_dir
        if not frames_dir.exists():
            raise FileNotFoundError(f"Replay frames directory not found: {frames_dir}")
        files = sorted(path for path in frames_dir.iterdir() if path.suffix.lower() in SUPPORTED_FRAME_SUFFIXES)
        if limit is not None:
            files = files[: max(0, int(limit))]

        root = self.replay_root
        labels = _read_json(root / "labels.json")
        expected_decisions = _read_json(root / "expected_decisions.json")
        expected_zones = _read_json(root / "expected_zones.json")
        expected_traps = _read_json(root / "expected_traps.json")
        expected_quality = _read_json(root / "expected_entry_quality.json")
        frame_metadata = _read_json(root / "frame_metadata.json")
        scenario_metadata = _read_json(root / "scenario.json")

        loaded: list[ReplayFrame] = []
        previous_timestamp = 0.0
        for index, path in enumerate(files):
            frame_id = _frame_number(path, index + 1)
            sidecar = _read_json(path.with_suffix(".json"))
            metadata: dict[str, Any] = {
                **scenario_metadata,
                **_lookup(frame_metadata, frame_id, path),
                **sidecar,
            }
            frame_labels = _lookup(labels, frame_id, path)
            expected = {
                **_mapping(_lookup(expected_decisions, frame_id, path).get("expected")),
                **_lookup(expected_decisions, frame_id, path),
            }
            zone_expected = _lookup(expected_zones, frame_id, path)
            trap_expected = _lookup(expected_traps, frame_id, path)
            quality_expected = _lookup(expected_quality, frame_id, path)
            if zone_expected:
                expected["zones"] = zone_expected
            if trap_expected:
                expected["trap"] = trap_expected.get("trap", trap_expected)
            if quality_expected:
                expected["entry_quality"] = quality_expected.get("entry_quality", quality_expected)

            timestamp = float(metadata.get("timestamp", metadata.get("timestamp_seconds", index)))
            interval = float(metadata.get("frame_interval_seconds", max(0.0, timestamp - previous_timestamp)))
            previous_timestamp = timestamp
            loaded.append(
                ReplayFrame(
                    frame_id=frame_id,
                    path=path,
                    sequence_index=index,
                    timestamp=timestamp,
                    frame_interval_seconds=interval,
                    metadata=metadata,
                    labels=frame_labels,
                    expected=expected,
                    frame_hash=str(metadata.get("frame_hash") or _hash_file(path)),
                )
            )
        return loaded


def load_replay_frames(replay_root: str | Path, *, limit: int | None = None) -> list[ReplayFrame]:
    return ReplayLoader(replay_root).load(limit=limit)
