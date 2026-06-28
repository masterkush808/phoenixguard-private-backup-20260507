from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNTIME_DIR = Path(os.getenv("PHOENIXGUARD_RUNTIME_DIR") or ROOT / "runtime" / "live").resolve()
ARCHIVE_ROOT = ROOT / "_archive" / "runtime_backup"
PRESERVE_RUNTIME_FILES = {
    "floating_window_v2.json",
}
PRESERVE_ROOT_FILES = {
    "808_shooter_boxes.json",
    "user_calibration_manifest.json",
    "Backend/config/shooter_3_gate_state.json",
}
CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache"}
SKIP_SCAN_DIR_NAMES = {
    ".git",
    ".venv",
    "_archive",
    ".codex_runtime",
    ".hf_cache",
    "runtime",
    "808 Memory",
    "book knowledge",
    "data",
    "data_splits",
    "memory_bank",
    "models",
    "node_modules",
    "reports",
}


def _timestamp() -> str:
    return time.strftime("%Y%m%d_%H%M%S")


def _remove_tree_or_file(path: Path) -> None:
    last_error: OSError | None = None
    for attempt in range(8):
        try:
            if path.is_dir():
                shutil.rmtree(path)
            elif path.exists():
                path.unlink()
            return
        except OSError as exc:
            last_error = exc
            time.sleep(min(1.0, 0.15 * (attempt + 1)))
    if last_error is not None:
        raise last_error


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


def delete_path(path: Path, moved: list[dict[str, str]], *, reason: str, apply: bool) -> None:
    rel = path.relative_to(ROOT)
    moved.append(
        {
            "original_path": rel.as_posix(),
            "new_path": "",
            "reason": reason,
            "classification": "safe to delete",
            "applied": str(bool(apply)),
            "action": "delete",
        }
    )
    if not apply:
        return
    _remove_tree_or_file(path)


def collect_runtime_paths() -> list[tuple[Path, str]]:
    paths: list[tuple[Path, str]] = []
    if RUNTIME_DIR.exists():
        for child in sorted(RUNTIME_DIR.iterdir()):
            if child.name in PRESERVE_RUNTIME_FILES:
                continue
            paths.append((child, "stale active runtime artifact"))
    for dirpath, dirnames, filenames in os.walk(ROOT):
        current = Path(dirpath)
        dirnames[:] = [name for name in dirnames if name not in SKIP_SCAN_DIR_NAMES]
        for dirname in list(dirnames):
            if dirname not in CACHE_DIR_NAMES:
                continue
            path = current / dirname
            paths.append((path, "python/test cache directory"))
            dirnames.remove(dirname)
        for filename in filenames:
            path = current / filename
            if path.name.startswith("shooter_debug.log"):
                paths.append((path, "old shooter debug log"))
            elif path.name.endswith(".pyc"):
                paths.append((path, "compiled python cache"))
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description="Back up and clear stale PhoenixGuard V3 runtime state.")
    parser.add_argument("--apply", action="store_true", help="Move runtime/cache files into _archive/runtime_backup.")
    parser.add_argument("--delete", action="store_true", help="Delete stale runtime/cache files instead of archiving them.")
    parser.add_argument("--manifest-file", default="", help="Write the planned/applied cleanup manifest to this path.")
    args = parser.parse_args()

    backup_root = ARCHIVE_ROOT / _timestamp()
    moved: list[dict[str, str]] = []
    if not args.delete:
        backup_root.mkdir(parents=True, exist_ok=True)
    for path, reason in collect_runtime_paths():
        if not path.exists():
            continue
        rel_path = path.relative_to(ROOT).as_posix()
        if path.is_file() and (path.name in PRESERVE_ROOT_FILES or rel_path in PRESERVE_ROOT_FILES):
            continue
        if args.delete:
            delete_path(path, moved, reason=reason, apply=args.apply)
        else:
            move_path(path, backup_root, moved, reason=reason, apply=args.apply)

    log: dict[str, object] = {
        "applied": bool(args.apply),
        "action": "delete" if args.delete else "archive",
        "runtime_dir": str(RUNTIME_DIR),
        "backup_root": "" if args.delete else backup_root.relative_to(ROOT).as_posix(),
        "preserved_runtime_files": sorted(PRESERVE_RUNTIME_FILES),
        "preserved_root_files": sorted(PRESERVE_ROOT_FILES),
        "preserved_scan_roots": sorted(SKIP_SCAN_DIR_NAMES),
        "moved": moved,
    }
    if not args.delete:
        (backup_root / "runtime_cleanup_log.json").write_text(json.dumps(log, indent=2, sort_keys=True), encoding="utf-8")
    if args.manifest_file:
        manifest_path = Path(args.manifest_file)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(log, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Runtime cleanup {'applied' if args.apply else 'dry-run'}")
    print(f"Action: {'delete' if args.delete else 'archive'}")
    if not args.delete:
        print(f"Backup root: {backup_root}")
    print(f"Paths {'deleted' if args.delete and args.apply else ('moved' if args.apply else 'planned')}: {len(moved)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
