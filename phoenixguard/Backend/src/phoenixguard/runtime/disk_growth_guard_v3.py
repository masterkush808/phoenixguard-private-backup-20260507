from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import json
import os
from pathlib import Path
import shutil
import stat
import time
from typing import Iterable, Iterator, Mapping, Sequence

from phoenixguard.paths import BUSINESS_ROOT, PROJECT_ROOT


class DiskGrowthGuardMode(str, Enum):
    OLDEST_FILES = "oldest_files"
    RESET_CHILDREN = "reset_children"


@dataclass(frozen=True, slots=True)
class DiskGrowthGuardTarget:
    name: str
    path: Path
    max_bytes: int
    low_water_bytes: int
    mode: DiskGrowthGuardMode = DiskGrowthGuardMode.OLDEST_FILES
    min_age_seconds: float = 120.0
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class DiskGrowthGuardAction:
    target: str
    path: str
    action: str
    bytes_removed: int
    reason: str
    applied: bool


@dataclass(frozen=True, slots=True)
class DiskGrowthGuardTargetReport:
    name: str
    path: str
    exists: bool
    max_bytes: int
    low_water_bytes: int
    bytes_before: int
    bytes_after: int
    triggered: bool
    actions: tuple[DiskGrowthGuardAction, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class DiskGrowthGuardReport:
    schema_version: str
    created_epoch_ms: int
    project_root: str
    applied: bool
    targets: tuple[DiskGrowthGuardTargetReport, ...]
    protected_roots: tuple[str, ...]

    @property
    def total_bytes_before(self) -> int:
        return sum(target.bytes_before for target in self.targets)

    @property
    def total_bytes_after(self) -> int:
        return sum(target.bytes_after for target in self.targets)

    @property
    def total_bytes_removed(self) -> int:
        return sum(action.bytes_removed for target in self.targets for action in target.actions)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "created_epoch_ms": self.created_epoch_ms,
            "project_root": self.project_root,
            "applied": self.applied,
            "total_bytes_before": self.total_bytes_before,
            "total_bytes_after": self.total_bytes_after,
            "total_bytes_removed": self.total_bytes_removed,
            "protected_roots": list(self.protected_roots),
            "targets": [
                {
                    "name": target.name,
                    "path": target.path,
                    "exists": target.exists,
                    "max_bytes": target.max_bytes,
                    "low_water_bytes": target.low_water_bytes,
                    "bytes_before": target.bytes_before,
                    "bytes_after": target.bytes_after,
                    "triggered": target.triggered,
                    "actions": [
                        {
                            "target": action.target,
                            "path": action.path,
                            "action": action.action,
                            "bytes_removed": action.bytes_removed,
                            "reason": action.reason,
                            "applied": action.applied,
                        }
                        for action in target.actions
                    ],
                }
                for target in self.targets
            ],
        }


class DiskGrowthGuardError(RuntimeError):
    pass


DEFAULT_MAX_BYTES = 512 * 1024 * 1024
DEFAULT_LOW_WATER_RATIO = 0.75
DEFAULT_MIN_AGE_SECONDS = 120.0


def parse_size_bytes(value: object, *, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, int):
        return max(0, value)
    raw = str(value).strip()
    if not raw:
        return default
    normalized = raw.replace("_", "").replace(" ", "").lower()
    multipliers: Mapping[str, int] = {
        "b": 1,
        "kb": 1024,
        "k": 1024,
        "mb": 1024 * 1024,
        "m": 1024 * 1024,
        "gb": 1024 * 1024 * 1024,
        "g": 1024 * 1024 * 1024,
    }
    for suffix, multiplier in sorted(multipliers.items(), key=lambda item: len(item[0]), reverse=True):
        if normalized.endswith(suffix):
            number = normalized[: -len(suffix)]
            return max(0, int(float(number) * multiplier))
    return max(0, int(float(normalized)))


def _resolve_existing_or_parent(path: Path) -> Path:
    current = path
    while not current.exists() and current.parent != current:
        current = current.parent
    return current.resolve()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _is_reparse_point(path: Path) -> bool:
    """Return true for symlinks and Windows junction/reparse entries."""

    try:
        is_symlink = getattr(path, "is_symlink", None)
        if callable(is_symlink) and bool(is_symlink()):
            return True
        lstat = getattr(path, "lstat", None)
        attributes = int(getattr(lstat(), "st_file_attributes", 0)) if callable(lstat) else 0
    except OSError:
        return True
    reparse_flag = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & reparse_flag)


def _safe_descendants(path: Path) -> Iterator[Path]:
    """Yield contained descendants without traversing filesystem indirections."""

    if _is_reparse_point(path) or not path.exists() or not path.is_dir():
        return
    root = path.resolve()
    pending = [path]
    while pending:
        directory = pending.pop()
        try:
            children = tuple(directory.iterdir())
        except OSError:
            continue
        for child in children:
            if _is_reparse_point(child):
                continue
            try:
                resolved = child.resolve()
            except OSError:
                continue
            if not _is_relative_to(resolved, root):
                continue
            yield child
            try:
                if child.is_dir():
                    pending.append(child)
            except OSError:
                continue


def _is_safe_member(path: Path, *, root: Path) -> bool:
    if _is_reparse_point(path) or _is_reparse_point(root):
        return False
    try:
        return _is_relative_to(path.resolve(), root.resolve())
    except OSError:
        return False


def _contains_reparse_descendant(path: Path) -> bool:
    if _is_reparse_point(path):
        return True
    pending = [path]
    while pending:
        directory = pending.pop()
        try:
            children = tuple(directory.iterdir())
        except OSError:
            return True
        for child in children:
            if _is_reparse_point(child):
                return True
            try:
                if child.is_dir():
                    pending.append(child)
            except OSError:
                return True
    return False


def _has_reparse_component(path: Path, *, allowed_roots: Sequence[Path]) -> bool:
    lexical_path = Path(os.path.abspath(path.expanduser()))
    lexical_roots = tuple(Path(os.path.abspath(root.expanduser())) for root in allowed_roots)
    matching_root = next((root for root in lexical_roots if _is_relative_to(lexical_path, root)), None)
    if matching_root is None:
        return True
    current = lexical_path
    while True:
        if current.exists() and _is_reparse_point(current):
            return True
        if current == matching_root:
            return False
        if current.parent == current:
            return True
        current = current.parent


def protected_roots(project_root: Path = PROJECT_ROOT) -> tuple[Path, ...]:
    candidates = (
        project_root / "models",
        project_root / "book knowledge",
        project_root / "808 Memory",
        project_root / "memory_bank",
        project_root / "data",
        project_root / "data_splits",
        project_root / "config",
        project_root / "Backend" / "src",
        project_root / "Frontend" / "dashboard" / "static",
    )
    return tuple(path.resolve() for path in candidates if path.exists())


def assert_safe_target(path: Path, *, allowed_roots: Sequence[Path], protected: Sequence[Path]) -> Path:
    if _has_reparse_component(path, allowed_roots=allowed_roots):
        raise DiskGrowthGuardError(f"Refusing disk guard target through a symlink, junction, or reparse point: {path}")
    intended = path.expanduser().resolve()
    existing_or_parent = _resolve_existing_or_parent(path)
    if not any(
        _is_relative_to(intended, root.resolve()) or _is_relative_to(existing_or_parent, root.resolve())
        for root in allowed_roots
    ):
        raise DiskGrowthGuardError(f"Refusing disk guard target outside allowed roots: {path}")
    for root in allowed_roots:
        if intended == root.resolve():
            raise DiskGrowthGuardError(f"Refusing to prune allowed root itself: {intended}")
    if any(
        intended == protected_root
        or existing_or_parent == protected_root
        or _is_relative_to(intended, protected_root)
        or _is_relative_to(existing_or_parent, protected_root)
        for protected_root in protected
    ):
        raise DiskGrowthGuardError(f"Refusing disk guard target inside protected root: {intended}")
    return intended


def directory_size(path: Path) -> int:
    try:
        if _is_reparse_point(path) or not path.exists():
            return 0
        if path.is_file():
            return path.stat().st_size
    except OSError:
        return 0
    total = 0
    try:
        for file_path in _safe_descendants(path):
            try:
                if file_path.is_file():
                    total += file_path.stat().st_size
            except OSError:
                continue
    except OSError:
        # Concurrent cleanup may remove a directory while rglob is traversing it.
        # Keep the bytes already observed and retry from fresh state next cycle.
        pass
    return total


def _file_entries(path: Path, *, now_epoch: float, min_age_seconds: float) -> list[tuple[float, int, Path]]:
    rows: list[tuple[float, int, Path]] = []
    if _is_reparse_point(path) or not path.exists():
        return rows
    if path.is_file():
        stat = path.stat()
        if now_epoch - stat.st_mtime >= min_age_seconds:
            rows.append((stat.st_mtime, stat.st_size, path))
        return rows
    for file_path in _safe_descendants(path):
        try:
            if not file_path.is_file():
                continue
            stat = file_path.stat()
            if now_epoch - stat.st_mtime < min_age_seconds:
                continue
            rows.append((stat.st_mtime, stat.st_size, file_path))
        except OSError:
            continue
    rows.sort(key=lambda row: row[0])
    return rows


def _child_entries(path: Path, *, now_epoch: float, min_age_seconds: float) -> list[tuple[float, int, Path]]:
    rows: list[tuple[float, int, Path]] = []
    if _is_reparse_point(path) or not path.exists() or not path.is_dir():
        return rows
    for child in path.iterdir():
        try:
            if not _is_safe_member(child, root=path):
                continue
            child_stat = child.stat()
            if child.is_file():
                if now_epoch - child_stat.st_mtime < min_age_seconds:
                    continue
                rows.append((child_stat.st_mtime, child_stat.st_size, child))
                continue
            if not child.is_dir():
                continue
            total_size = 0
            oldest_mtime = child_stat.st_mtime
            has_recent_file = False
            has_file = False
            for file_path in _safe_descendants(child):
                if not file_path.is_file():
                    continue
                has_file = True
                file_stat = file_path.stat()
                total_size += file_stat.st_size
                oldest_mtime = min(oldest_mtime, file_stat.st_mtime)
                if now_epoch - file_stat.st_mtime < min_age_seconds:
                    has_recent_file = True
                    break
            if has_recent_file:
                continue
            if not has_file:
                if now_epoch - child_stat.st_mtime < min_age_seconds:
                    continue
                total_size = directory_size(child)
            rows.append((oldest_mtime, total_size, child))
        except OSError:
            continue
    rows.sort(key=lambda row: row[0])
    return rows


def _remove(path: Path, *, target_root: Path) -> bool:
    try:
        if not _is_safe_member(path, root=target_root):
            return False
        if path.is_dir():
            if _contains_reparse_descendant(path):
                return False
            shutil.rmtree(path)
        elif path.exists():
            path.unlink(missing_ok=True)
        return not path.exists()
    except OSError:
        # Active Windows log handles can temporarily refuse deletion. Keep the
        # guard alive and retry on the next sweep instead of crashing it.
        return False


def _remove_empty_dirs(path: Path) -> None:
    if _is_reparse_point(path) or not path.exists() or not path.is_dir():
        return
    dirs = sorted(
        (p for p in _safe_descendants(path) if p.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    )
    for directory in dirs:
        try:
            directory.rmdir()
        except OSError:
            continue


def _prune_oldest_files(target: DiskGrowthGuardTarget, *, apply: bool) -> tuple[int, tuple[DiskGrowthGuardAction, ...]]:
    current_size = directory_size(target.path)
    actions: list[DiskGrowthGuardAction] = []
    if current_size <= target.max_bytes:
        return current_size, tuple(actions)
    for _mtime, size, file_path in _file_entries(
        target.path,
        now_epoch=time.time(),
        min_age_seconds=target.min_age_seconds,
    ):
        removed = not apply or _remove(file_path, target_root=target.path)
        actions.append(
            DiskGrowthGuardAction(
                target=target.name,
                path=str(file_path),
                action="delete_file",
                bytes_removed=size if removed else 0,
                reason=f"target exceeded {target.max_bytes} bytes",
                applied=bool(apply and removed),
            )
        )
        if apply:
            if not removed:
                continue
        current_size = max(0, current_size - size)
        if current_size <= target.low_water_bytes:
            break
    if apply:
        _remove_empty_dirs(target.path)
        current_size = directory_size(target.path)
    return current_size, tuple(actions)


def _reset_children(target: DiskGrowthGuardTarget, *, apply: bool) -> tuple[int, tuple[DiskGrowthGuardAction, ...]]:
    current_size = directory_size(target.path)
    actions: list[DiskGrowthGuardAction] = []
    if not target.path.exists() or not target.path.is_dir():
        return current_size, tuple(actions)
    if current_size <= target.max_bytes:
        if apply and target.max_bytes == 0:
            try:
                target.path.rmdir()
            except OSError:
                pass
        return current_size, tuple(actions)
    for _mtime, size, child in _child_entries(
        target.path,
        now_epoch=time.time(),
        min_age_seconds=target.min_age_seconds,
    ):
        removed = not apply or _remove(child, target_root=target.path)
        actions.append(
            DiskGrowthGuardAction(
                target=target.name,
                path=str(child),
                action="delete_child",
                bytes_removed=size if removed else 0,
                reason=f"target exceeded {target.max_bytes} bytes",
                applied=bool(apply and removed),
            )
        )
        if apply:
            if not removed:
                continue
        current_size = max(0, current_size - size)
        if current_size <= target.low_water_bytes:
            break
    if apply:
        current_size = directory_size(target.path)
        if target.max_bytes == 0 and current_size == 0:
            try:
                target.path.rmdir()
            except OSError:
                pass
    return current_size, tuple(actions)


def run_disk_growth_guard(
    targets: Iterable[DiskGrowthGuardTarget],
    *,
    apply: bool,
    allowed_roots: Sequence[Path] | None = None,
    protected: Sequence[Path] | None = None,
) -> DiskGrowthGuardReport:
    allowed = tuple(path.resolve() for path in (allowed_roots or (PROJECT_ROOT, Path.home() / ".codex")))
    protected_paths = tuple(protected or protected_roots(PROJECT_ROOT))
    reports: list[DiskGrowthGuardTargetReport] = []
    for target in targets:
        if not target.enabled:
            continue
        safe_path = assert_safe_target(target.path, allowed_roots=allowed, protected=protected_paths)
        normalized = DiskGrowthGuardTarget(
            name=target.name,
            path=safe_path,
            max_bytes=target.max_bytes,
            low_water_bytes=min(target.low_water_bytes, target.max_bytes),
            mode=target.mode,
            min_age_seconds=max(0.0, target.min_age_seconds),
            enabled=target.enabled,
        )
        before = directory_size(normalized.path)
        if normalized.mode is DiskGrowthGuardMode.RESET_CHILDREN:
            after, actions = _reset_children(normalized, apply=apply)
        else:
            after, actions = _prune_oldest_files(normalized, apply=apply)
        reports.append(
            DiskGrowthGuardTargetReport(
                name=normalized.name,
                path=str(normalized.path),
                exists=normalized.path.exists(),
                max_bytes=normalized.max_bytes,
                low_water_bytes=normalized.low_water_bytes,
                bytes_before=before,
                bytes_after=after,
                triggered=before > normalized.max_bytes,
                actions=actions,
            )
        )
    return DiskGrowthGuardReport(
        schema_version="PG_DISK_GROWTH_GUARD_V3",
        created_epoch_ms=int(time.time() * 1000),
        project_root=str(PROJECT_ROOT),
        applied=apply,
        targets=tuple(reports),
        protected_roots=tuple(str(path) for path in protected_paths),
    )


def build_default_targets(
    *,
    max_bytes: int | None = None,
    low_water_bytes: int | None = None,
    include_codex_sessions: bool = False,
    project_root: Path = PROJECT_ROOT,
) -> tuple[DiskGrowthGuardTarget, ...]:
    cap = max_bytes if max_bytes is not None else parse_size_bytes(os.getenv("PHOENIXGUARD_DISK_GUARD_MAX_BYTES"), default=DEFAULT_MAX_BYTES)
    low = low_water_bytes if low_water_bytes is not None else int(cap * DEFAULT_LOW_WATER_RATIO)
    runtime_dir = Path(os.getenv("PHOENIXGUARD_RUNTIME_DIR") or project_root / "runtime" / "live")
    reports_dir = project_root / "reports"
    codex_runtime_dir = project_root / ".codex_runtime"
    archive_dir = project_root / "_archive"
    session_cap = min(cap, parse_size_bytes(os.getenv("PHOENIXGUARD_SESSION_RUNTIME_MAX_BYTES"), default=256 * 1024 * 1024))
    session_low = min(low, parse_size_bytes(os.getenv("PHOENIXGUARD_SESSION_RUNTIME_LOW_BYTES"), default=128 * 1024 * 1024))
    registry_cap = min(cap, parse_size_bytes(os.getenv("PHOENIXGUARD_MARKET_REGISTRY_MAX_BYTES"), default=16 * 1024 * 1024))
    registry_low = min(low, parse_size_bytes(os.getenv("PHOENIXGUARD_MARKET_REGISTRY_LOW_BYTES"), default=8 * 1024 * 1024))
    live_log_cap = min(cap, parse_size_bytes(os.getenv("PHOENIXGUARD_LIVE_LOG_MAX_BYTES"), default=64 * 1024 * 1024))
    live_log_low = min(low, parse_size_bytes(os.getenv("PHOENIXGUARD_LIVE_LOG_LOW_BYTES"), default=32 * 1024 * 1024))
    debug_cap = min(cap, parse_size_bytes(os.getenv("PHOENIXGUARD_OVERLAY_DEBUG_MAX_BYTES"), default=16 * 1024 * 1024))
    debug_low = min(low, parse_size_bytes(os.getenv("PHOENIXGUARD_OVERLAY_DEBUG_LOW_BYTES"), default=4 * 1024 * 1024))
    report_cap = min(cap, parse_size_bytes(os.getenv("PHOENIXGUARD_REPORT_MAX_BYTES"), default=256 * 1024 * 1024))
    report_low = min(low, parse_size_bytes(os.getenv("PHOENIXGUARD_REPORT_LOW_BYTES"), default=128 * 1024 * 1024))
    launcher_log_cap = min(cap, parse_size_bytes(os.getenv("PHOENIXGUARD_LAUNCHER_LOG_MAX_BYTES"), default=16 * 1024 * 1024))
    targets: list[DiskGrowthGuardTarget] = [
        DiskGrowthGuardTarget(
            name="live_window_tracker_sessions",
            path=runtime_dir / "data_live" / "mobile_api" / "window_tracker" / "sessions",
            max_bytes=session_cap,
            low_water_bytes=min(session_low, session_cap),
            mode=DiskGrowthGuardMode.OLDEST_FILES,
            min_age_seconds=DEFAULT_MIN_AGE_SECONDS,
        ),
        DiskGrowthGuardTarget(
            name="market_registry",
            path=runtime_dir / "data_live" / "market_registry",
            max_bytes=registry_cap,
            low_water_bytes=min(registry_low, registry_cap),
            mode=DiskGrowthGuardMode.OLDEST_FILES,
            min_age_seconds=0.0,
        ),
        DiskGrowthGuardTarget(
            name="live_logs",
            path=runtime_dir / "logs_live",
            max_bytes=live_log_cap,
            low_water_bytes=min(live_log_low, live_log_cap),
            mode=DiskGrowthGuardMode.OLDEST_FILES,
            min_age_seconds=DEFAULT_MIN_AGE_SECONDS,
        ),
        DiskGrowthGuardTarget(
            name="runtime_logs",
            path=runtime_dir / "logs",
            max_bytes=live_log_cap,
            low_water_bytes=min(live_log_low, live_log_cap),
            mode=DiskGrowthGuardMode.OLDEST_FILES,
            min_age_seconds=DEFAULT_MIN_AGE_SECONDS,
        ),
        DiskGrowthGuardTarget(
            name="tracker_launcher_stdout",
            path=runtime_dir / "tracker_launcher_stdout.log",
            max_bytes=launcher_log_cap,
            low_water_bytes=0,
            mode=DiskGrowthGuardMode.OLDEST_FILES,
            min_age_seconds=DEFAULT_MIN_AGE_SECONDS,
        ),
        DiskGrowthGuardTarget(
            name="tracker_launcher_stderr",
            path=runtime_dir / "tracker_launcher_stderr.log",
            max_bytes=launcher_log_cap,
            low_water_bytes=0,
            mode=DiskGrowthGuardMode.OLDEST_FILES,
            min_age_seconds=DEFAULT_MIN_AGE_SECONDS,
        ),
        DiskGrowthGuardTarget(
            name="overlay_persist_logs",
            path=runtime_dir / "overlay_persist_logs",
            max_bytes=debug_cap,
            low_water_bytes=min(debug_low, debug_cap),
            mode=DiskGrowthGuardMode.OLDEST_FILES,
            min_age_seconds=DEFAULT_MIN_AGE_SECONDS,
        ),
        DiskGrowthGuardTarget(
            name="reports",
            path=reports_dir,
            max_bytes=report_cap,
            low_water_bytes=min(report_low, report_cap),
            mode=DiskGrowthGuardMode.OLDEST_FILES,
            min_age_seconds=DEFAULT_MIN_AGE_SECONDS,
        ),
        DiskGrowthGuardTarget(
            name="codex_runtime",
            path=codex_runtime_dir,
            max_bytes=cap,
            low_water_bytes=low,
            mode=DiskGrowthGuardMode.OLDEST_FILES,
            min_age_seconds=DEFAULT_MIN_AGE_SECONDS,
        ),
        DiskGrowthGuardTarget(
            name="legacy_archive",
            path=archive_dir,
            max_bytes=0,
            low_water_bytes=0,
            mode=DiskGrowthGuardMode.RESET_CHILDREN,
            min_age_seconds=0.0,
        ),
        DiskGrowthGuardTarget(
            name="business_next_cache",
            path=BUSINESS_ROOT / "web" / ".next",
            max_bytes=min(cap, parse_size_bytes(os.getenv("PHOENIXGUARD_DISK_GUARD_NEXT_CACHE_MAX_BYTES"), default=512 * 1024 * 1024)),
            low_water_bytes=min(low, parse_size_bytes(os.getenv("PHOENIXGUARD_DISK_GUARD_NEXT_CACHE_LOW_BYTES"), default=256 * 1024 * 1024)),
            mode=DiskGrowthGuardMode.RESET_CHILDREN,
            min_age_seconds=DEFAULT_MIN_AGE_SECONDS,
        ),
    ]
    if include_codex_sessions:
        targets.append(
            DiskGrowthGuardTarget(
                name="operator_codex_sessions",
                path=Path.home() / ".codex" / "sessions",
                max_bytes=cap,
                low_water_bytes=low,
                mode=DiskGrowthGuardMode.RESET_CHILDREN,
                min_age_seconds=max(6 * 60 * 60, DEFAULT_MIN_AGE_SECONDS),
            )
        )
    return tuple(targets)


def write_guard_report(path: Path, report: DiskGrowthGuardReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
