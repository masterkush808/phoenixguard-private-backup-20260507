from __future__ import annotations

import argparse
import json
import shutil
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_DIR = ROOT / ".codex_runtime"
ARCHIVE_ROOT = ROOT / "_archive" / "runtime_backup"
PRESERVE_RUNTIME_FILES = {
    "floating_window_v2.json",
}
PRESERVE_ROOT_FILES = {
    "808_shooter_boxes.json",
    "user_calibration_manifest.json",
    "shooter_3_gate_state.json",
}
CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache"}


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def move_path(path: Path, backup_root: Path, moved: list[dict[str, str]], *, reason: str, apply: bool) -> None:
    rel = path.relative_to(ROOT)
    destination = backup_root / rel
    moved.append(
        {
            "original_path": rel.as_posix(),
            "new_path": destination.relative_to(ROOT).as_posix(),
            "reason": reason,
            "applied": str(bool(apply)),
        }
    )
    if not apply:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    if path.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.move(str(path), str(destination))
    elif path.exists():
        if destination.exists():
            destination.unlink()
        shutil.move(str(path), str(destination))


def collect_runtime_paths() -> list[tuple[Path, str]]:
    paths: list[tuple[Path, str]] = []
    if RUNTIME_DIR.exists():
        for child in sorted(RUNTIME_DIR.iterdir()):
            if child.name in PRESERVE_RUNTIME_FILES:
                continue
            paths.append((child, "stale .codex_runtime artifact"))
    for path in ROOT.rglob("*"):
        if path.parts and any(part in {".git", ".venv", "_archive"} for part in path.parts):
            continue
        if path.name in CACHE_DIR_NAMES:
            paths.append((path, "python/test cache directory"))
        elif path.is_file() and path.name.startswith("shooter_debug.log"):
            paths.append((path, "old shooter debug log"))
        elif path.is_file() and path.name.endswith(".pyc"):
            paths.append((path, "compiled python cache"))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up and clear stale PhoenixGuard V3 runtime state.")
    parser.add_argument("--apply", action="store_true", help="Move runtime/cache files into _archive/runtime_backup.")
    args = parser.parse_args()

    backup_root = ARCHIVE_ROOT / _timestamp()
    moved: list[dict[str, str]] = []
    backup_root.mkdir(parents=True, exist_ok=True)
    for path, reason in collect_runtime_paths():
        if not path.exists():
            continue
        if path.is_file() and path.name in PRESERVE_ROOT_FILES:
            continue
        move_path(path, backup_root, moved, reason=reason, apply=args.apply)

    log = {
        "applied": bool(args.apply),
        "backup_root": backup_root.relative_to(ROOT).as_posix(),
        "preserved_runtime_files": sorted(PRESERVE_RUNTIME_FILES),
        "preserved_root_files": sorted(PRESERVE_ROOT_FILES),
        "moved": moved,
    }
    (backup_root / "runtime_cleanup_log.json").write_text(json.dumps(log, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Runtime cleanup {'applied' if args.apply else 'dry-run'}")
    print(f"Backup root: {backup_root}")
    print(f"Paths {'moved' if args.apply else 'planned'}: {len(moved)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
