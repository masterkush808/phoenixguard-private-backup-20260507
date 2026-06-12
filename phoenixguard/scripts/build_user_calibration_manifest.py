from __future__ import annotations

import argparse
import json
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from phoenixguard.execution.calibration_manifest import (
    MANIFEST_FILENAME,
    SOURCE_BOXES_FILENAME,
    build_manifest,
    create_deletion_report,
    list_uncalibrated_artifacts,
    write_manifest,
)

DEFAULT_ARTIFACT_PATTERNS = (
    "808_shooter_boxes.json",
    "808_calibration_report.json",
    "*calibration*.json",
    "*calibrator*.py",
    "*click*.py",
    "*executor*.py",
    "*shooter*boxes*.json",
    "shooter_debug.log",
    "shooter_debug.log.*",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the authoritative PhoenixGuard user calibration manifest.")
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--profile-id", default="default")
    parser.add_argument("--layout-id", default=None)
    parser.add_argument("--boxes", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--backup-dir", type=Path, default=None)
    parser.add_argument("--no-backup", action="store_true")
    args = parser.parse_args()

    repo_root = args.repo_root.resolve()
    boxes_path = (args.boxes or repo_root / SOURCE_BOXES_FILENAME).resolve()
    output_path = (args.output or repo_root / MANIFEST_FILENAME).resolve()
    backup_dir = (args.backup_dir or repo_root / ".codex_runtime" / "calibration_backups").resolve()

    artifacts = _scan_artifacts(repo_root)
    backup_report = _backup_artifacts(artifacts, backup_dir) if not args.no_backup else _backup_report(None, artifacts)
    manifest = build_manifest(
        boxes_path,
        profile_id=args.profile_id,
        layout_id=args.layout_id,
        artifact_paths=artifacts,
    )
    uncalibrated = list_uncalibrated_artifacts(manifest, profile_id=args.profile_id, layout_id=args.layout_id)
    manifest["backup"] = backup_report
    manifest["deletion_report"] = create_deletion_report(uncalibrated)

    write_manifest(manifest, output_path)
    print(json.dumps({"manifest_path": str(output_path), "backup": backup_report, "uncalibrated": uncalibrated}, indent=2))
    return 0


def _scan_artifacts(repo_root: Path) -> list[Path]:
    found: list[Path] = []
    for pattern in DEFAULT_ARTIFACT_PATTERNS:
        found.extend(path for path in repo_root.glob(pattern) if path.is_file())
    return sorted(set(found))


def _backup_artifacts(artifacts: list[Path], backup_dir: Path) -> dict[str, Any]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = backup_dir / f"user_calibration_artifacts_{stamp}.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for artifact in artifacts:
            archive.write(artifact, arcname=artifact.name)
    return _backup_report(archive_path, artifacts)


def _backup_report(archive_path: Path | None, artifacts: list[Path]) -> dict[str, Any]:
    return {
        "archive_path": str(archive_path) if archive_path is not None else None,
        "artifact_count": len(artifacts),
        "artifacts": [str(path) for path in artifacts],
    }


if __name__ == "__main__":
    raise SystemExit(main())
