from __future__ import annotations

import os
from pathlib import Path
import time

import pytest

from phoenixguard.runtime.disk_growth_guard_v3 import (
    DiskGrowthGuardError,
    DiskGrowthGuardMode,
    DiskGrowthGuardTarget,
    assert_safe_target,
    directory_size,
    parse_size_bytes,
    run_disk_growth_guard,
)


def _write(path: Path, size: int, *, age_seconds: float = 3600.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    old = time.time() - age_seconds
    os.utime(path, (old, old))


def test_parse_size_bytes_accepts_server_friendly_units() -> None:
    assert parse_size_bytes("2GB", default=0) == 2 * 1024 * 1024 * 1024
    assert parse_size_bytes("1536MB", default=0) == 1536 * 1024 * 1024
    assert parse_size_bytes("", default=123) == 123


def test_disk_growth_guard_prunes_generated_files_to_low_water(tmp_path: Path) -> None:
    reports = tmp_path / "reports"
    _write(reports / "old-a.png", 8)
    _write(reports / "nested" / "old-b.png", 8)
    models = tmp_path / "models"
    _write(models / "keep.bin", 32)

    target = DiskGrowthGuardTarget(
        name="reports",
        path=reports,
        max_bytes=10,
        low_water_bytes=4,
        mode=DiskGrowthGuardMode.OLDEST_FILES,
        min_age_seconds=1.0,
    )

    report = run_disk_growth_guard(
        [target],
        apply=True,
        allowed_roots=(tmp_path,),
        protected=(models,),
    )

    assert report.targets[0].triggered is True
    assert report.total_bytes_removed == 16
    assert directory_size(reports) <= 4
    assert (models / "keep.bin").exists()


def test_disk_growth_guard_refuses_protected_assets(tmp_path: Path) -> None:
    models = tmp_path / "models"
    _write(models / "weights.bin", 32)

    with pytest.raises(DiskGrowthGuardError):
        assert_safe_target(models, allowed_roots=(tmp_path,), protected=(models,))


def test_disk_growth_guard_reset_children_removes_old_cache_children(tmp_path: Path) -> None:
    cache = tmp_path / "Business" / "web" / ".next"
    _write(cache / "dev" / "cache-a.bin", 16)
    _write(cache / "server" / "cache-b.bin", 16)

    target = DiskGrowthGuardTarget(
        name="business_next_cache",
        path=cache,
        max_bytes=10,
        low_water_bytes=0,
        mode=DiskGrowthGuardMode.RESET_CHILDREN,
        min_age_seconds=1.0,
    )
    report = run_disk_growth_guard([target], apply=True, allowed_roots=(tmp_path,), protected=())

    assert report.targets[0].triggered is True
    assert report.total_bytes_removed == 32
    assert directory_size(cache) == 0
    assert cache.exists()


def test_disk_growth_guard_does_not_delete_recent_live_files(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime" / "live" / "data_live"
    _write(runtime / "fresh.jpg", 32, age_seconds=0.0)

    target = DiskGrowthGuardTarget(
        name="live_window_tracker_artifacts",
        path=runtime,
        max_bytes=10,
        low_water_bytes=0,
        mode=DiskGrowthGuardMode.OLDEST_FILES,
        min_age_seconds=3600.0,
    )
    report = run_disk_growth_guard([target], apply=True, allowed_roots=(tmp_path,), protected=())

    assert report.targets[0].triggered is True
    assert report.total_bytes_removed == 0
    assert (runtime / "fresh.jpg").exists()
