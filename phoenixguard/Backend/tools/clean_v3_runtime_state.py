from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
ROOT_RESOLVED = ROOT.resolve()
EXPECTED_RUNTIME_DIR = (ROOT / "runtime" / "live").resolve()
RUNTIME_DIR = Path(os.getenv("PHOENIXGUARD_RUNTIME_DIR") or EXPECTED_RUNTIME_DIR).resolve()
LEGACY_ARCHIVE_DIR = ROOT / "_archive"
LEGACY_RUNTIME_BACKUP_DIR = (ROOT / "_archive" / "runtime_backup").resolve()
PRESERVE_RUNTIME_FILES = {
    "floating_window_v2.json",
}
PRESERVE_ROOT_FILES = {
    "808_shooter_boxes.json",
    "user_calibration_manifest.json",
    "Backend/config/shooter_3_gate_state.json",
}
CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache"}
DISPOSABLE_ROOT_PATHS = {
    ".codex/tmp",
    ".codex_runtime",
    ".ruff_cache",
    "Business/web/.next",
    "Business/web/test-results",
    "cleanup_reports",
    "debug.log",
    "logs",
    "reports",
}
DISPOSABLE_FILE_PATHS = {
    "Backend/scripts_runtime/replay_trace.log",
    "Business/web/reports/product_dashboard_source_console_smoke.png",
    "Business/web/tsconfig.tsbuildinfo",
}
SKIP_SCAN_DIR_NAMES = {
    ".git",
    "_archive",
    ".codex_runtime",
    ".hf_cache",
    "808 Memory",
    "book knowledge",
    "data",
    "data_splits",
    "memory_bank",
    "models",
    "node_modules",
}


def _is_virtual_environment_dir(path: Path) -> bool:
    name = path.name.lower()
    return name == ".venv" or name.startswith(".venv-") or (path / "pyvenv.cfg").is_file()


def _is_reparse_point(path: Path) -> bool:
    try:
        file_attributes = int(getattr(path.lstat(), "st_file_attributes", 0))
    except OSError:
        return False
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))
    return path.is_symlink() or bool(reparse_flag and file_attributes & reparse_flag)


def _assert_safe_delete_target(path: Path, *, exact_relative: str | None = None) -> Path:
    """Reject path escapes and Windows junction/symlink redirection before deletion."""

    lexical = Path(os.path.abspath(path))
    try:
        lexical.relative_to(ROOT_RESOLVED)
    except ValueError as exc:
        raise RuntimeError(f"Refusing to delete outside the PhoenixGuard root: {path}") from exc
    if lexical == ROOT_RESOLVED:
        raise RuntimeError("Refusing to delete the PhoenixGuard repository root")
    if exact_relative is not None and lexical != ROOT_RESOLVED / exact_relative:
        raise RuntimeError(f"Refusing unexpected generated path: {path}")

    current = lexical
    while current != ROOT_RESOLVED:
        if current.exists() and _is_reparse_point(current):
            raise RuntimeError(f"Refusing to follow a symlink or junction during cleanup: {current}")
        current = current.parent

    resolved = lexical.resolve(strict=False)
    try:
        resolved.relative_to(ROOT_RESOLVED)
    except ValueError as exc:
        raise RuntimeError(f"Refusing resolved cleanup path outside the repository: {resolved}") from exc
    return lexical


def _assert_expected_runtime_dir() -> None:
    if RUNTIME_DIR != EXPECTED_RUNTIME_DIR:
        raise RuntimeError(
            "Refusing to clean runtime outside the canonical live runtime directory: "
            f"PHOENIXGUARD_RUNTIME_DIR={RUNTIME_DIR}; expected={EXPECTED_RUNTIME_DIR}"
        )


def _remove_tree_or_file(path: Path) -> None:
    if path.is_dir() and _contains_reparse_descendant(path):
        raise RuntimeError(
            f"Refusing to delete a tree containing a symlink or junction: {path}"
        )
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


def _contains_reparse_descendant(path: Path) -> bool:
    pending = [path]
    while pending:
        current = pending.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            child = Path(entry.path)
            if _is_reparse_point(child):
                return True
            try:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(child)
            except OSError:
                continue
    return False


def delete_path(path: Path, moved: list[dict[str, str]], *, reason: str, apply: bool) -> None:
    path = _assert_safe_delete_target(path)
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
    _assert_expected_runtime_dir()
    paths: list[tuple[Path, str]] = []
    if RUNTIME_DIR.exists():
        for child in sorted(RUNTIME_DIR.iterdir()):
            if child.name in PRESERVE_RUNTIME_FILES:
                continue
            child = _assert_safe_delete_target(child)
            if child.parent != RUNTIME_DIR:
                raise RuntimeError(f"Refusing to collect unexpected runtime child: {child}")
            paths.append((child, "stale active runtime artifact"))
    if LEGACY_ARCHIVE_DIR.exists():
        legacy_archive = _assert_safe_delete_target(
            LEGACY_ARCHIVE_DIR,
            exact_relative="_archive",
        )
        paths.append((legacy_archive, "legacy disposable archive root"))
    for relative_path in sorted(DISPOSABLE_ROOT_PATHS):
        disposable_path = ROOT / relative_path
        if not disposable_path.exists():
            continue
        disposable_path = _assert_safe_delete_target(
            disposable_path,
            exact_relative=relative_path,
        )
        paths.append((disposable_path, "generated report/log/cache root"))
    for relative_path in sorted(DISPOSABLE_FILE_PATHS):
        disposable_file = ROOT / relative_path
        if not disposable_file.exists():
            continue
        disposable_file = _assert_safe_delete_target(
            disposable_file,
            exact_relative=relative_path,
        )
        paths.append((disposable_file, "generated report/log/cache file"))
    for dirpath, dirnames, filenames in os.walk(ROOT):
        current = Path(dirpath)
        retained_dirnames: list[str] = []
        for name in dirnames:
            candidate = current / name
            if name in SKIP_SCAN_DIR_NAMES or _is_virtual_environment_dir(candidate):
                continue
            if current == ROOT and name in {"runtime", "reports"}:
                continue
            retained_dirnames.append(name)
        dirnames[:] = retained_dirnames
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
    parser = argparse.ArgumentParser(
        description="Permanently clear disposable PhoenixGuard V3 runtime/cache state without creating archives."
    )
    parser.add_argument("--apply", action="store_true", help="Permanently delete the planned runtime/cache paths.")
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Compatibility flag; cleanup is delete-only and never creates a backup archive.",
    )
    parser.add_argument("--manifest-file", default="", help="Write the planned/applied cleanup manifest to this path.")
    args = parser.parse_args()

    moved: list[dict[str, str]] = []
    for path, reason in collect_runtime_paths():
        if not path.exists():
            continue
        rel_path = path.relative_to(ROOT).as_posix()
        if path.is_file() and (path.name in PRESERVE_ROOT_FILES or rel_path in PRESERVE_ROOT_FILES):
            continue
        delete_path(path, moved, reason=reason, apply=args.apply)
    log: dict[str, object] = {
        "applied": bool(args.apply),
        "action": "delete",
        "archive_created": False,
        "legacy_archive_dir": str(LEGACY_ARCHIVE_DIR),
        "legacy_runtime_backup_dir": str(LEGACY_RUNTIME_BACKUP_DIR),
        "runtime_dir": str(RUNTIME_DIR),
        "preserved_runtime_files": sorted(PRESERVE_RUNTIME_FILES),
        "preserved_root_files": sorted(PRESERVE_ROOT_FILES),
        "preserved_scan_roots": sorted(SKIP_SCAN_DIR_NAMES),
        "disposable_root_paths": sorted(DISPOSABLE_ROOT_PATHS),
        "disposable_file_paths": sorted(DISPOSABLE_FILE_PATHS),
        "moved": moved,
    }
    if args.manifest_file:
        manifest_path = Path(args.manifest_file)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(log, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Runtime cleanup {'applied' if args.apply else 'dry-run'}")
    print("Action: delete (archive creation disabled)")
    print(f"Paths {'deleted' if args.apply else 'planned'}: {len(moved)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
