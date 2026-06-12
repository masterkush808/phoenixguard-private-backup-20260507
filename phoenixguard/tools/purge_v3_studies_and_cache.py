from __future__ import annotations

import argparse
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[1]

DIRECT_RUNTIME_DIRS = {
    "studies": "generated V3 runtime study records",
    "study_packets": "generated V3 runtime study packets",
    "replay_studies": "generated V3 replay study records",
    "old_study_cache": "obsolete generated V3 study cache",
    "visual_state_cache": "generated visual-state cache",
    "overlay_cache": "generated overlay cache",
    "frame_cache": "generated frame cache",
    "stale_sessions": "generated stale runtime sessions",
}
LATEST_RUNTIME_PATTERNS = {
    "latest_study*.json": "generated latest V3 study pointer",
    "latest_execution*.json": "generated latest V3 execution pointer",
}
DATA_LIVE_DIR_NAMES = {
    "studies": "generated data_live V3 study records",
    "study_packets": "generated data_live V3 study packets",
}

PROTECTED_EXACT_NAMES = {
    "808_shooter_boxes.json",
    "user_calibration_manifest.json",
    "V3_CANONICAL_MANIFEST.json",
    "V3_LANGUAGE_CONSTITUTION.md",
}
PROTECTED_PARTS = {
    ".git",
    ".venv",
    "_archive",
    "reports",
    "tests",
}
PROTECTED_WEIGHT_SUFFIXES = {
    ".bin",
    ".ckpt",
    ".gguf",
    ".h5",
    ".joblib",
    ".onnx",
    ".pb",
    ".pkl",
    ".pt",
    ".pth",
    ".safetensors",
    ".tflite",
}


@dataclass(frozen=True)
class PurgeRecord:
    path: Path
    reason: str
    file_count: int
    total_size: int
    safe_to_delete: bool
    protected_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class PurgeResult:
    root: Path
    report_path: Path
    confirm_delete: bool
    records: tuple[PurgeRecord, ...]

    @property
    def total_file_count(self) -> int:
        return sum(record.file_count for record in self.records)

    @property
    def total_size(self) -> int:
        return sum(record.total_size for record in self.records)


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _normalized_relative_parts(path: Path, root: Path) -> tuple[str, ...]:
    try:
        rel = path.relative_to(root)
    except ValueError:
        rel = path
    return tuple(part.lower() for part in rel.parts)


def _relative_posix(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_under_runtime(path: Path, runtime: Path) -> bool:
    if path.is_symlink():
        return False
    return _is_relative_to(path.resolve(strict=False), runtime.resolve(strict=False))


def is_protected_path(path: Path, root: Path) -> bool:
    parts = _normalized_relative_parts(path, root)
    name = path.name
    normalized = "/".join(parts)
    if path.is_symlink():
        return True
    if name in PROTECTED_EXACT_NAMES:
        return True
    if path.suffix.lower() in PROTECTED_WEIGHT_SUFFIXES:
        return True
    if any(part in PROTECTED_PARTS for part in parts):
        return True
    if "calibration" in normalized:
        return True
    if "memory_source" in parts or ("curated" in normalized and "memory" in normalized):
        return True
    if "contract" in normalized and ("v3" in normalized or name == "contracts.py"):
        return True
    return False


def _iter_files(path: Path, root: Path) -> Iterable[Path]:
    if path.is_file() or path.is_symlink():
        yield path
        return
    for child in sorted(path.iterdir()):
        if is_protected_path(child, root):
            yield child
            continue
        if child.is_dir():
            yield from _iter_files(child, root)
        else:
            yield child


def _build_record(path: Path, root: Path, runtime: Path, reason: str) -> PurgeRecord | None:
    if not path.exists() and not path.is_symlink():
        return None
    if not _is_under_runtime(path, runtime):
        return None

    if is_protected_path(path, root):
        return None

    files: list[Path] = []
    protected: list[Path] = []
    for candidate in _iter_files(path, root):
        if is_protected_path(candidate, root):
            protected.append(candidate)
        elif candidate.is_file():
            files.append(candidate)

    total_size = 0
    for file_path in files:
        try:
            total_size += file_path.stat().st_size
        except OSError:
            continue

    return PurgeRecord(
        path=path,
        reason=reason,
        file_count=len(files),
        total_size=total_size,
        safe_to_delete=True,
        protected_paths=tuple(sorted(protected)),
    )


def _dedupe_targets(targets: Iterable[tuple[Path, str]]) -> list[tuple[Path, str]]:
    seen: set[Path] = set()
    deduped: list[tuple[Path, str]] = []
    for path, reason in targets:
        key = path.resolve(strict=False)
        if key in seen:
            continue
        seen.add(key)
        deduped.append((path, reason))
    return deduped


def iter_purge_records(root: Path) -> tuple[PurgeRecord, ...]:
    root = root.resolve()
    runtime = root / ".codex_runtime"
    if not runtime.exists() or runtime.is_symlink():
        return ()

    targets: list[tuple[Path, str]] = []
    for name, reason in DIRECT_RUNTIME_DIRS.items():
        targets.append((runtime / name, reason))
    for pattern, reason in LATEST_RUNTIME_PATTERNS.items():
        targets.extend((path, reason) for path in sorted(runtime.glob(pattern)))

    data_live = runtime / "data_live"
    if data_live.exists() and not data_live.is_symlink():
        for path in sorted(data_live.rglob("*")):
            if path.is_dir() and path.name in DATA_LIVE_DIR_NAMES:
                targets.append((path, DATA_LIVE_DIR_NAMES[path.name]))

    records: list[PurgeRecord] = []
    for target, reason in _dedupe_targets(targets):
        record = _build_record(target, root, runtime, reason)
        if record is not None:
            records.append(record)
    return tuple(records)


def _delete_record(record: PurgeRecord, root: Path, runtime: Path) -> None:
    if not _is_under_runtime(record.path, runtime):
        raise RuntimeError(f"Refusing to purge outside .codex_runtime: {record.path}")
    if is_protected_path(record.path, root):
        return

    if not record.protected_paths:
        if record.path.is_dir():
            shutil.rmtree(record.path)
        elif record.path.exists() and record.path.is_file():
            record.path.unlink()
        return

    safe_files = sorted(
        (path for path in _iter_files(record.path, root) if path.is_file()),
        reverse=True,
    )
    for file_path in safe_files:
        if is_protected_path(file_path, root):
            continue
        if not _is_under_runtime(file_path, runtime):
            raise RuntimeError(f"Refusing to purge outside .codex_runtime: {file_path}")
        file_path.unlink()

    if record.path.is_dir():
        directories = sorted(
            (path for path in record.path.rglob("*") if path.is_dir()),
            key=lambda item: len(item.parts),
            reverse=True,
        )
        for directory in directories:
            if is_protected_path(directory, root):
                continue
            try:
                directory.rmdir()
            except OSError:
                pass
        try:
            record.path.rmdir()
        except OSError:
            pass


def _format_bytes(size: int) -> str:
    return str(size)


def render_report(result: PurgeResult) -> str:
    mode = "delete" if result.confirm_delete else "dry-run"
    lines = [
        "# Final Purged Studies And Cache Report",
        "",
        f"- mode: {mode}",
        f"- root: `{result.root.as_posix()}`",
        f"- total_file_count: {result.total_file_count}",
        f"- total_size_bytes: {result.total_size}",
        "",
        "| path | file_count | total_size_bytes | reason | safe_to_delete |",
        "| --- | ---: | ---: | --- | --- |",
    ]
    if result.records:
        for record in result.records:
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"`{_relative_posix(record.path, result.root)}`",
                        str(record.file_count),
                        _format_bytes(record.total_size),
                        record.reason,
                        "true" if record.safe_to_delete else "false",
                    ]
                )
                + " |"
            )
    else:
        lines.append("| `_none_` | 0 | 0 | no generated V3 studies/cache found | true |")

    protected_paths = sorted({path for record in result.records for path in record.protected_paths})
    if protected_paths:
        lines.extend(
            [
                "",
                "## Protected Paths Retained",
                "",
                "| path | reason |",
                "| --- | --- |",
            ]
        )
        for path in protected_paths:
            lines.append(f"| `{_relative_posix(path, result.root)}` | protected by purge guard |")

    return "\n".join(lines).rstrip() + "\n"


def write_report(result: PurgeResult) -> None:
    result.report_path.parent.mkdir(parents=True, exist_ok=True)
    result.report_path.write_text(render_report(result), encoding="utf-8")


def run_purge(root: Path, *, confirm_delete: bool = False, report_path: Path | None = None) -> PurgeResult:
    root = root.resolve()
    runtime = root / ".codex_runtime"
    report_path = (
        report_path or root / "reports" / "FINAL_PURGED_STUDIES_AND_CACHE_REPORT.md"
    ).resolve()
    records = iter_purge_records(root)
    result = PurgeResult(root=root, report_path=report_path, confirm_delete=confirm_delete, records=records)

    if confirm_delete:
        for record in records:
            _delete_record(record, root, runtime)

    write_report(result)
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely purge generated PhoenixGuard V3 runtime studies and cache artifacts.",
    )
    parser.add_argument("--root", type=Path, default=ROOT, help="Repository root. Defaults to this tool's repository.")
    parser.add_argument(
        "--confirm-delete",
        action="store_true",
        help="Actually delete allowlisted generated runtime studies/cache. Omit for dry-run.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Markdown report path. Defaults to reports/FINAL_PURGED_STUDIES_AND_CACHE_REPORT.md under --root.",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    result = run_purge(args.root, confirm_delete=args.confirm_delete, report_path=args.report)
    action = "deleted" if args.confirm_delete else "planned"
    print(f"V3 studies/cache purge {action}: {result.total_file_count} files, {result.total_size} bytes")
    print(f"Report: {result.report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
