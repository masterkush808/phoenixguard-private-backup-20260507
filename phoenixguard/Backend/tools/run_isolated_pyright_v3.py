from __future__ import annotations

import argparse
from collections.abc import Sequence
import os
from pathlib import Path
import re
import subprocess
import sys
import time


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCH_SIZE = 10
MAX_BATCH_SIZE = 50
DEFAULT_NODE_HEAP_MB = 512
DEFAULT_BATCH_TIMEOUT_SEC = 600.0
EXCLUDED_DIR_NAMES = frozenset(
    {
        ".codex_runtime",
        ".git",
        ".hf_cache",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".venv",
        ".venv-business",
        ".venv-dev",
        ".venv-docs",
        ".venv-live",
        ".venv-training",
        "__pycache__",
        "_archive",
        "_backups",
        "cleanup_reports",
        "logs",
        "node_modules",
        "reports",
    }
)
EXCLUDED_ROOT_DIR_NAMES = frozenset({"reports", "runtime"})
EXCLUDED_RELATIVE_PREFIXES = ("Business/web", "DataAnalysisExpert")
NODE_HEAP_OPTION = re.compile(r"(?:^|\s)--max-old-space-size=\d+(?=\s|$)")


def _is_excluded_relative(relative_path: str) -> bool:
    normalized = relative_path.replace("\\", "/").strip("/")
    return any(
        normalized == prefix or normalized.startswith(f"{prefix}/")
        for prefix in EXCLUDED_RELATIVE_PREFIXES
    )


def discover_python_files(repo_root: Path = REPO_ROOT) -> tuple[str, ...]:
    root = repo_root.resolve()
    discovered: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        current = Path(dirpath)
        try:
            relative_dir = current.relative_to(root).as_posix()
        except ValueError:
            continue
        dirnames[:] = sorted(
            dirname
            for dirname in dirnames
            if dirname not in EXCLUDED_DIR_NAMES
            and not dirname.lower().startswith(".venv")
            and not (relative_dir == "." and dirname in EXCLUDED_ROOT_DIR_NAMES)
            and not _is_excluded_relative(
                f"{relative_dir}/{dirname}" if relative_dir != "." else dirname
            )
        )
        if _is_excluded_relative(relative_dir):
            dirnames[:] = []
            continue
        for filename in sorted(filenames):
            if Path(filename).suffix.lower() not in {".py", ".pyi"}:
                continue
            path = current / filename
            relative = path.relative_to(root).as_posix()
            if not _is_excluded_relative(relative):
                discovered.append(relative)
    return tuple(sorted(dict.fromkeys(discovered)))


def build_batches(files: Sequence[str], batch_size: int) -> tuple[tuple[str, ...], ...]:
    if not 1 <= int(batch_size) <= MAX_BATCH_SIZE:
        raise ValueError(f"batch_size must be between 1 and {MAX_BATCH_SIZE}")
    normalized = tuple(sorted(dict.fromkeys(str(path) for path in files if str(path).strip())))
    return tuple(
        normalized[start : start + int(batch_size)]
        for start in range(0, len(normalized), int(batch_size))
    )


def bounded_node_options(current: str, max_old_space_mb: int) -> str:
    without_existing_cap = NODE_HEAP_OPTION.sub(" ", str(current or "")).strip()
    cap = f"--max-old-space-size={max(256, int(max_old_space_mb))}"
    return f"{without_existing_cap} {cap}".strip()


def run_batches(
    batches: Sequence[Sequence[str]],
    *,
    repo_root: Path = REPO_ROOT,
    max_old_space_mb: int = DEFAULT_NODE_HEAP_MB,
    batch_timeout_sec: float = DEFAULT_BATCH_TIMEOUT_SEC,
) -> int:
    if not batches:
        print("PYRIGHT_ISOLATED_FAIL no Python files discovered", flush=True)
        return 2
    environment = os.environ.copy()
    environment["NODE_OPTIONS"] = bounded_node_options(
        environment.get("NODE_OPTIONS", ""),
        max_old_space_mb,
    )
    total_files = sum(len(batch) for batch in batches)
    for index, batch in enumerate(batches, start=1):
        started = time.monotonic()
        command = [
            sys.executable,
            "-m",
            "pyright",
            "--threads",
            "1",
            "--warnings",
            *batch,
        ]
        try:
            completed = subprocess.run(
                command,
                cwd=str(repo_root),
                env=environment,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=max(30.0, float(batch_timeout_sec)),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            print(
                f"PYRIGHT_BATCH_FAIL {index}/{len(batches)} timeout={exc.timeout}s files={len(batch)}",
                flush=True,
            )
            return 3
        output = str(completed.stdout or "").strip()
        if completed.returncode != 0:
            if output:
                print(output, flush=True)
            print(
                f"PYRIGHT_BATCH_FAIL {index}/{len(batches)} exit={completed.returncode} files={len(batch)}",
                flush=True,
            )
            return int(completed.returncode or 1)
        elapsed = time.monotonic() - started
        print(
            f"PYRIGHT_BATCH_PASS {index}/{len(batches)} files={len(batch)} seconds={elapsed:.1f}",
            flush=True,
        )
    print(
        f"PYRIGHT_ISOLATED_PASS files={total_files} batches={len(batches)} errors=0 warnings=0",
        flush=True,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run strict repository-wide Pyright in fresh memory-capped batches.",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--node-heap-mb", type=int, default=DEFAULT_NODE_HEAP_MB)
    parser.add_argument("--batch-timeout-sec", type=float, default=DEFAULT_BATCH_TIMEOUT_SEC)
    parser.add_argument("--list-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    files = discover_python_files(REPO_ROOT)
    batches = build_batches(files, int(args.batch_size))
    if bool(args.list_only):
        print(
            f"PYRIGHT_ISOLATED_PLAN files={len(files)} batches={len(batches)} "
            f"batch_size={int(args.batch_size)} node_heap_mb={max(256, int(args.node_heap_mb))}",
        )
        return 0
    return run_batches(
        batches,
        repo_root=REPO_ROOT,
        max_old_space_mb=int(args.node_heap_mb),
        batch_timeout_sec=float(args.batch_timeout_sec),
    )


if __name__ == "__main__":
    raise SystemExit(main())
