from __future__ import annotations

from pathlib import Path
import json
import re
from typing import Mapping, cast

import pytest

from phoenixguard.vision import market_registry


def _overlay(entry: Mapping[str, object]) -> Mapping[str, object]:
    value = entry.get("overlay")
    return cast(Mapping[str, object], value) if isinstance(value, Mapping) else {}


def test_market_registry_compacts_in_place_without_debug_archives(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_dir = tmp_path / "market_registry"
    runtime_dir = tmp_path / "runtime" / "live"
    monkeypatch.setattr(market_registry, "REGISTRY_DIR", registry_dir)
    monkeypatch.setenv("PHOENIXGUARD_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("PHOENIXGUARD_MARKET_REGISTRY_MAX_BYTES", "4096")
    monkeypatch.setenv("PHOENIXGUARD_MARKET_REGISTRY_RETAIN_LINES", "24")
    monkeypatch.delenv("PHOENIXGUARD_OVERLAY_PERSIST_DEBUG", raising=False)

    session_id = "bounded-live-session"
    for index in range(80):
        market_registry.persist_market_objects(
            session_id,
            [
                {
                    "id": f"object-{index}",
                    "bbox": [1, 2, 3, 4],
                    "truth_score": 0.9,
                    "detail": "x" * 128,
                }
            ],
            chart_transform={"chart_transform_id": f"ct-{index}", "frame_id": index},
        )

    registry_path = registry_dir / f"{session_id}.jsonl"
    assert registry_path.stat().st_size <= 4096
    assert not (runtime_dir / "overlay_persist_logs").exists()
    assert not list(registry_dir.glob("*.tmp"))

    entries = market_registry.load_market_objects(session_id)
    assert entries
    assert _overlay(entries[-1]).get("id") == "object-79"


def test_market_registry_debug_dump_is_explicit_opt_in(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_dir = tmp_path / "market_registry"
    runtime_dir = tmp_path / "runtime" / "live"
    monkeypatch.setattr(market_registry, "REGISTRY_DIR", registry_dir)
    monkeypatch.setenv("PHOENIXGUARD_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("PHOENIXGUARD_OVERLAY_PERSIST_DEBUG", "1")

    market_registry.persist_market_objects(
        "debug-opt-in",
        [{"id": "one", "bbox": [1, 2, 3, 4], "truth_score": 0.9}],
    )

    dumps = list((runtime_dir / "overlay_persist_logs").glob("*.json"))
    assert len(dumps) == 1
    assert re.fullmatch(r"[A-Za-z0-9_-]+_[0-9a-f]{32}\.json", dumps[0].name)


def test_arbitrary_session_id_is_stable_and_cannot_escape_registry_or_debug_roots(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_dir = tmp_path / "market_registry"
    runtime_dir = tmp_path / "runtime" / "live"
    monkeypatch.setattr(market_registry, "REGISTRY_DIR", registry_dir)
    monkeypatch.setenv("PHOENIXGUARD_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("PHOENIXGUARD_OVERLAY_PERSIST_DEBUG", "1")
    session_id = r"..\..\outside/CON:*?<>|"

    first = market_registry.persist_market_objects(
        session_id,
        [{"id": "one", "bbox": [1, 2, 3, 4], "truth_score": 0.9}],
    )
    second = market_registry.persist_market_objects(
        session_id,
        [{"id": "two", "bbox": [2, 3, 4, 5], "truth_score": 0.9}],
    )

    assert first == second
    assert first.resolve().is_relative_to(registry_dir.resolve())
    assert ".." not in first.name
    assert re.fullmatch(r"[A-Za-z0-9_-]+\.jsonl", first.name)
    assert len(market_registry.load_market_objects(session_id)) == 2
    assert not list(tmp_path.glob("outside*.jsonl"))

    debug_files = list((runtime_dir / "overlay_persist_logs").glob("*.json"))
    assert len(debug_files) == 2
    assert all(path.resolve().is_relative_to(runtime_dir.resolve()) for path in debug_files)
    assert all(
        re.fullmatch(r"[A-Za-z0-9_-]+_[0-9a-f]{32}\.json", path.name)
        for path in debug_files
    )


def test_oversized_record_uses_bounded_truth_and_geometry_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_dir = tmp_path / "market_registry"
    monkeypatch.setattr(market_registry, "REGISTRY_DIR", registry_dir)
    monkeypatch.setenv("PHOENIXGUARD_MARKET_REGISTRY_RECORD_MAX_BYTES", "4096")
    monkeypatch.setenv("PHOENIXGUARD_MARKET_REGISTRY_MAX_BYTES", "65536")

    path = market_registry.persist_market_objects(
        "oversized-record",
        [
            {
                "id": "precision-zone-1",
                "object_id": "object-1",
                "track_id": "track-1",
                "lifecycle_state": "CONFIRMED",
                "truth_score": 0.93,
                "bbox": [10.0, 20.0, 30.0, 40.0],
                "detail": "x" * 200_000,
            }
        ],
        chart_transform={"chart_transform_id": "ct-1", "frame_id": 17},
    )

    encoded_lines = path.read_bytes().splitlines(keepends=True)
    assert len(encoded_lines) == 1
    assert len(encoded_lines[0]) <= 4096
    raw = cast(dict[str, object], json.loads(encoded_lines[0]))
    assert raw["record_compacted"] is True
    assert raw["overlay_id"] == "precision-zone-1"
    assert raw["object_id"] == "object-1"
    assert raw["track_id"] == "track-1"
    assert raw["lifecycle_state"] == "CONFIRMED"
    assert raw["truth_score"] == 0.93
    compact_overlay = cast(dict[str, object], raw["overlay"])
    assert compact_overlay["bbox"] == [10.0, 20.0, 30.0, 40.0]
    assert "detail" not in compact_overlay


def test_compactor_drops_legacy_single_line_above_low_water(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_dir = tmp_path / "market_registry"
    registry_dir.mkdir(parents=True)
    monkeypatch.setattr(market_registry, "REGISTRY_DIR", registry_dir)
    monkeypatch.setenv("PHOENIXGUARD_MARKET_REGISTRY_MAX_BYTES", "4096")
    monkeypatch.setenv("PHOENIXGUARD_MARKET_REGISTRY_RETAIN_LINES", "24")
    path = registry_dir / "legacy-oversized.jsonl"
    path.write_text(json.dumps({"legacy": "x" * 20_000}) + "\n", encoding="utf-8")

    market_registry.persist_market_objects(
        "legacy-oversized",
        [{"id": "fresh", "bbox": [1, 2, 3, 4], "truth_score": 0.91}],
    )

    retained = path.read_bytes()
    assert 0 < len(retained) <= 2048
    assert b'"legacy"' not in retained
    assert b'"id":"fresh"' in retained
    assert not list(registry_dir.glob("*.tmp"))
