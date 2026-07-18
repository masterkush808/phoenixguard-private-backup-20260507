from __future__ import annotations

import argparse
import ast
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import TextIO
import xml.etree.ElementTree as ElementTree


REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_ROOT_PREFIX = "Backend/tests/"
REAL_MODEL_TEST_FILE = "Backend/tests/test_real_models.py"
REAL_SENTENCE_TRANSFORMER_NODE_PREFIX = (
    "Backend/tests/test_real_models.py::TestSentenceTransformerEmbedder::"
)
MAX_SAFE_TESTS_PER_PROCESS = 25
ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")

# These libraries own native runtimes, model allocators, or browser child
# processes. Their files receive the native environment and bounded same-file
# batches. Only the explicit real-model certification file needs singleton
# boundaries because it constructs heavyweight provider models.
NATIVE_IMPORT_ROOTS = frozenset(
    {
        "chronos",
        "cv2",
        "onnxruntime",
        "playwright",
        "sentence_transformers",
        "tensorflow",
        "torch",
        "torchvision",
        "transformers",
        "ultralytics",
    }
)

# This suite reaches OpenCV and vision-native implementations transitively.
# Keep the declaration narrow and auditable rather than treating every NumPy
# test as a native-model test.
NATIVE_TRANSITIVE_TEST_FILES = frozenset(
    {"Backend/tests/vision/test_enhanced_vision_phase1.py"}
)

WINDOWS_NATIVE_EXIT_NAMES: dict[int, str] = {
    0xC0000005: "ACCESS_VIOLATION",
    0xC0000008: "INVALID_HANDLE",
    0xC0000017: "NO_MEMORY",
    0xC0000094: "INTEGER_DIVIDE_BY_ZERO",
    0xC0000135: "DLL_NOT_FOUND",
    0xC0000139: "ENTRYPOINT_NOT_FOUND",
    0xC00000FD: "STACK_OVERFLOW",
    0xC0000374: "HEAP_CORRUPTION",
    0xC0000409: "STACK_BUFFER_OVERRUN",
    0xE06D7363: "CPP_EXCEPTION",
}


@dataclass(frozen=True, slots=True)
class TestBatch:
    batch_id: str
    source_file: str
    nodeids: tuple[str, ...]
    lane: str
    isolation_reason: str


@dataclass(frozen=True, slots=True)
class JunitCounts:
    valid: bool
    tests: int
    failures: int
    errors: int
    skipped: int
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ExitClassification:
    label: str
    native_crash: bool
    unsigned_code: int


@dataclass(frozen=True, slots=True)
class BatchResult:
    batch_id: str
    source_file: str
    nodeids: tuple[str, ...]
    lane: str
    isolation_reason: str
    command: tuple[str, ...]
    return_code: int
    exit_label: str
    native_crash: bool
    timed_out: bool
    duration_seconds: float
    junit: JunitCounts
    coverage_verified: bool
    passed: bool
    log_path: str


@dataclass(frozen=True, slots=True)
class CoverageProof:
    collected_count: int
    collected_unique_count: int
    scheduled_count: int
    scheduled_unique_count: int
    verified_count: int
    verified_unique_count: int
    missing_from_schedule: tuple[str, ...]
    extra_in_schedule: tuple[str, ...]
    duplicate_scheduled_nodeids: tuple[str, ...]
    unverified_nodeids: tuple[str, ...]
    schedule_exact: bool
    execution_exact: bool


def _normalise_source_file(nodeid: str) -> str:
    return nodeid.split("::", 1)[0].replace("\\", "/")


def parse_collected_nodeids(output: str) -> tuple[str, ...]:
    """Extract exact pytest node IDs without deduplicating or hiding defects."""

    nodeids: list[str] = []
    for raw_line in output.splitlines():
        line = ANSI_ESCAPE_RE.sub("", raw_line).strip()
        if "::" not in line:
            continue
        source_file = _normalise_source_file(line)
        if source_file.startswith(TEST_ROOT_PREFIX) and source_file.endswith(".py"):
            nodeids.append(line)
    return tuple(nodeids)


def _native_imports_for_source(repo_root: Path, source_file: str) -> tuple[str, ...]:
    source_path = repo_root.joinpath(*source_file.split("/"))
    try:
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
    except (OSError, SyntaxError, UnicodeError):
        # Collection will report the actual source error. Isolation is the safe
        # scheduling response if the static inspection boundary is unavailable.
        return ("source_inspection_failed",)

    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return tuple(sorted(roots & NATIVE_IMPORT_ROOTS))


def _chunks(values: Sequence[str], size: int) -> tuple[tuple[str, ...], ...]:
    return tuple(tuple(values[start : start + size]) for start in range(0, len(values), size))


def build_schedule(
    nodeids: Sequence[str],
    *,
    repo_root: Path,
    max_tests_per_process: int = MAX_SAFE_TESTS_PER_PROCESS,
) -> tuple[TestBatch, ...]:
    if not 1 <= max_tests_per_process <= MAX_SAFE_TESTS_PER_PROCESS:
        raise ValueError(
            f"max_tests_per_process must be between 1 and {MAX_SAFE_TESTS_PER_PROCESS}"
        )
    if not nodeids:
        raise ValueError("pytest collection returned no test node IDs")

    grouped: dict[str, list[str]] = {}
    for nodeid in nodeids:
        source_file = _normalise_source_file(nodeid)
        grouped.setdefault(source_file, []).append(nodeid)

    batches: list[TestBatch] = []
    for source_file, source_nodes in grouped.items():
        native_imports = _native_imports_for_source(repo_root, source_file)
        if source_file == REAL_MODEL_TEST_FILE:
            sentence_nodes = tuple(
                nodeid
                for nodeid in source_nodes
                if nodeid.startswith(REAL_SENTENCE_TRANSFORMER_NODE_PREFIX)
            )
            sentence_batch_added = False
            for nodeid in source_nodes:
                if nodeid.startswith(REAL_SENTENCE_TRANSFORMER_NODE_PREFIX):
                    if sentence_batch_added:
                        continue
                    nodes = sentence_nodes
                    reason = "isolated_sentence_transformer_worker_contract"
                    sentence_batch_added = True
                else:
                    nodes = (nodeid,)
                    reason = "real_model_test"
                batches.append(
                    TestBatch(
                        batch_id=f"batch-{len(batches) + 1:04d}",
                        source_file=source_file,
                        nodeids=nodes,
                        lane="real_model",
                        isolation_reason=reason,
                    )
                )
            continue
        elif source_file in NATIVE_TRANSITIVE_TEST_FILES:
            lane = "native"
            isolation_reason = "transitive_native_runtime"
            batch_nodes = _chunks(source_nodes, max_tests_per_process)
        elif native_imports:
            lane = "native"
            isolation_reason = "native_import:" + ",".join(native_imports)
            batch_nodes = _chunks(source_nodes, max_tests_per_process)
        else:
            lane = "core"
            isolation_reason = "bounded_same_file_chunk"
            batch_nodes = _chunks(source_nodes, max_tests_per_process)

        for nodes in batch_nodes:
            batches.append(
                TestBatch(
                    batch_id=f"batch-{len(batches) + 1:04d}",
                    source_file=source_file,
                    nodeids=nodes,
                    lane=lane,
                    isolation_reason=isolation_reason,
                )
            )

    proof = build_coverage_proof(nodeids, batches, verified_nodeids=())
    if not proof.schedule_exact:
        raise RuntimeError(
            "isolated pytest schedule does not exactly match collection: "
            f"missing={len(proof.missing_from_schedule)} "
            f"extra={len(proof.extra_in_schedule)} "
            f"duplicates={len(proof.duplicate_scheduled_nodeids)}"
        )
    return tuple(batches)


def build_coverage_proof(
    collected_nodeids: Sequence[str],
    batches: Sequence[TestBatch],
    *,
    verified_nodeids: Sequence[str],
) -> CoverageProof:
    collected = Counter(collected_nodeids)
    scheduled_sequence = tuple(nodeid for batch in batches for nodeid in batch.nodeids)
    scheduled = Counter(scheduled_sequence)
    verified = Counter(verified_nodeids)

    missing = tuple(sorted((collected - scheduled).elements()))
    extra = tuple(sorted((scheduled - collected).elements()))
    duplicates = tuple(sorted(nodeid for nodeid, count in scheduled.items() if count > 1))
    unverified = tuple(sorted((collected - verified).elements()))
    schedule_exact = collected == scheduled and not duplicates
    execution_exact = schedule_exact and collected == verified
    return CoverageProof(
        collected_count=sum(collected.values()),
        collected_unique_count=len(collected),
        scheduled_count=sum(scheduled.values()),
        scheduled_unique_count=len(scheduled),
        verified_count=sum(verified.values()),
        verified_unique_count=len(verified),
        missing_from_schedule=missing,
        extra_in_schedule=extra,
        duplicate_scheduled_nodeids=duplicates,
        unverified_nodeids=unverified,
        schedule_exact=schedule_exact,
        execution_exact=execution_exact,
    )


def build_test_environment(
    base_environment: Mapping[str, str], *, lane: str
) -> dict[str, str]:
    environment = dict(base_environment)
    environment.update(
        {
            "CUDA_VISIBLE_DEVICES": "-1",
            "HF_DATASETS_OFFLINE": "1",
            "HF_HUB_OFFLINE": "1",
            "MKL_NUM_THREADS": "1",
            "MPLBACKEND": "Agg",
            "NO_COLOR": "1",
            "NUMEXPR_NUM_THREADS": "1",
            "OMP_NUM_THREADS": "1",
            "OPENBLAS_NUM_THREADS": "1",
            "PHOENIXGUARD_CHRONOS_CPU_THREADS": "1",
            "PHOENIXGUARD_BACKGROUND_WARMUP_ON_LAUNCH": "0",
            "PHOENIXGUARD_CV_ALLOW_REMOTE_BOOTSTRAP": "0",
            "PHOENIXGUARD_CV_ALLOW_REMOTE_ENDPOINT": "0",
            "PHOENIXGUARD_DISABLE_TRACKER_STOP_HOTKEY": "1",
            "PHOENIXGUARD_ENABLE_CHRONOS_MODEL_IN_TESTS": "0",
            "PHOENIXGUARD_ENABLE_LOCAL_YOLO_IN_TESTS": "0",
            "PHOENIXGUARD_PYTHON_PROFILE": "test",
            "PHOENIXGUARD_STRICT_REPO_VENV": "0",
            "PHOENIXGUARD_TEXT_EMBEDDER_ALLOW_REMOTE_BOOTSTRAP": "0",
            "PHOENIXGUARD_TRACING_DISABLED": "1",
            "PY_COLORS": "0",
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONIOENCODING": "utf-8",
            "TOKENIZERS_PARALLELISM": "false",
            "TRANSFORMERS_OFFLINE": "1",
        }
    )
    if lane == "real_model":
        # Real-model integration tests remain scheduled and may contact their
        # declared providers. They still run CPU-only, one node per process.
        environment["HF_DATASETS_OFFLINE"] = "0"
        environment["HF_HUB_OFFLINE"] = "0"
        environment["TRANSFORMERS_OFFLINE"] = "0"
    return environment


def classify_process_exit(return_code: int, *, timed_out: bool = False) -> ExitClassification:
    unsigned_code = return_code & 0xFFFFFFFF
    if timed_out:
        return ExitClassification("TIMEOUT", False, unsigned_code)
    if return_code == 0:
        return ExitClassification("PASS", False, 0)
    known_name = WINDOWS_NATIVE_EXIT_NAMES.get(unsigned_code)
    if known_name is not None:
        return ExitClassification(
            f"WINDOWS_NATIVE_{known_name}_0x{unsigned_code:08X}", True, unsigned_code
        )
    if return_code < 0:
        return ExitClassification(f"SIGNAL_{abs(return_code)}", True, unsigned_code)
    if 0xC0000000 <= unsigned_code <= 0xCFFFFFFF:
        return ExitClassification(
            f"WINDOWS_NATIVE_STATUS_0x{unsigned_code:08X}", True, unsigned_code
        )
    return ExitClassification(f"PYTEST_EXIT_{return_code}", False, unsigned_code)


def parse_junit_counts(path: Path) -> JunitCounts:
    if not path.is_file():
        return JunitCounts(False, 0, 0, 0, 0, "JUnit XML was not produced")
    try:
        root = ElementTree.parse(path).getroot()
    except (ElementTree.ParseError, OSError) as exc:
        return JunitCounts(False, 0, 0, 0, 0, f"invalid JUnit XML: {exc}")

    testcases = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "testcase"]
    failures = 0
    errors = 0
    skipped = 0
    for testcase in testcases:
        child_names = {child.tag.rsplit("}", 1)[-1] for child in testcase}
        failures += int("failure" in child_names)
        errors += int("error" in child_names)
        skipped += int("skipped" in child_names)
    return JunitCounts(True, len(testcases), failures, errors, skipped)


def _terminate_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        process.kill()


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "\n",
        encoding="utf-8",
    )


def _append_json_line(stream: TextIO, payload: object) -> None:
    stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str))
    stream.write("\n")
    stream.flush()
    os.fsync(stream.fileno())


def _coverage_payload(proof: CoverageProof, *, detail_limit: int = 40) -> dict[str, object]:
    payload = asdict(proof)
    for field_name in (
        "missing_from_schedule",
        "extra_in_schedule",
        "duplicate_scheduled_nodeids",
        "unverified_nodeids",
    ):
        values = tuple(getattr(proof, field_name))
        payload[field_name] = {
            "count": len(values),
            "sample": values[:detail_limit],
            "truncated": len(values) > detail_limit,
        }
    return payload


def _collection_command(python_executable: Path) -> tuple[str, ...]:
    return (
        str(python_executable),
        "-m",
        "pytest",
        "--collect-only",
        "-q",
        "-p",
        "no:cacheprovider",
    )


def collect_nodeids(
    *,
    python_executable: Path,
    repo_root: Path,
    timeout_seconds: float,
) -> tuple[tuple[str, ...], float]:
    command = _collection_command(python_executable)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=repo_root,
            env=build_test_environment(os.environ, lane="core"),
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1.0, timeout_seconds),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"pytest collection timed out after {timeout_seconds:.1f}s") from exc
    duration = time.monotonic() - started
    output = completed.stdout
    if completed.returncode != 0:
        tail = "\n".join(output.splitlines()[-40:])
        raise RuntimeError(
            f"pytest collection failed with exit {completed.returncode}:\n{tail}"
        )
    nodeids = parse_collected_nodeids(output)
    if not nodeids:
        raise RuntimeError("pytest collection completed but yielded no parseable test node IDs")
    return nodeids, duration


def _batch_command(
    python_executable: Path, batch: TestBatch, junit_path: Path
) -> tuple[str, ...]:
    return (
        str(python_executable),
        "-m",
        "pytest",
        "-q",
        "--tb=short",
        "-ra",
        "-p",
        "no:cacheprovider",
        f"--junitxml={junit_path}",
        *batch.nodeids,
    )


def run_batch(
    *,
    python_executable: Path,
    repo_root: Path,
    run_dir: Path,
    batch: TestBatch,
    timeout_seconds: float,
    keep_passing_logs: bool,
) -> BatchResult:
    junit_path = run_dir / f"{batch.batch_id}.junit.xml"
    log_path = run_dir / f"{batch.batch_id}.log"
    command = _batch_command(python_executable, batch, junit_path)
    creation_flags = int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)) if os.name == "nt" else 0
    started = time.monotonic()
    timed_out = False
    with log_path.open("w", encoding="utf-8", errors="replace") as log_stream:
        process: subprocess.Popen[bytes] = subprocess.Popen(
            command,
            cwd=repo_root,
            env=build_test_environment(os.environ, lane=batch.lane),
            stdout=log_stream,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
            start_new_session=os.name != "nt",
        )
        try:
            return_code = process.wait(timeout=max(1.0, timeout_seconds))
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_tree(process)
            try:
                return_code = process.wait(timeout=30.0)
            except subprocess.TimeoutExpired:
                process.kill()
                return_code = process.wait(timeout=30.0)
        except KeyboardInterrupt:
            _terminate_process_tree(process)
            raise

    duration = time.monotonic() - started
    exit_classification = classify_process_exit(return_code, timed_out=timed_out)
    junit = parse_junit_counts(junit_path)
    coverage_verified = not timed_out and junit.valid and junit.tests == len(batch.nodeids)
    passed = (
        coverage_verified
        and return_code == 0
        and junit.failures == 0
        and junit.errors == 0
    )
    retain_log = keep_passing_logs or not passed or junit.skipped > 0
    if not retain_log:
        log_path.unlink(missing_ok=True)
    return BatchResult(
        batch_id=batch.batch_id,
        source_file=batch.source_file,
        nodeids=batch.nodeids,
        lane=batch.lane,
        isolation_reason=batch.isolation_reason,
        command=command,
        return_code=return_code,
        exit_label=exit_classification.label,
        native_crash=exit_classification.native_crash,
        timed_out=timed_out,
        duration_seconds=round(duration, 3),
        junit=junit,
        coverage_verified=coverage_verified,
        passed=passed,
        log_path=str(log_path.relative_to(repo_root)) if log_path.exists() else "",
    )


def _manifest_payload(
    *,
    nodeids: Sequence[str],
    batches: Sequence[TestBatch],
    collection_seconds: float,
    python_executable: Path,
) -> dict[str, object]:
    encoded = "\n".join(nodeids).encode("utf-8")
    lanes = Counter(batch.lane for batch in batches)
    return {
        "schema_version": "PG_ISOLATED_PYTEST_MANIFEST_V1",
        "created_at": datetime.now(UTC).isoformat(),
        "python_executable": str(python_executable),
        "collection_seconds": round(collection_seconds, 3),
        "collected_count": len(nodeids),
        "collected_sha256": hashlib.sha256(encoded).hexdigest(),
        "batch_count": len(batches),
        "batch_lanes": dict(sorted(lanes.items())),
        "batches": [asdict(batch) for batch in batches],
    }


def _run_directory(output_root: Path) -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    candidate = output_root / f"{stamp}-{os.getpid()}"
    candidate.mkdir(parents=True, exist_ok=False)
    return candidate


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the complete PhoenixGuard pytest collection in sequential, "
            "memory-bounded child processes with an exact coverage proof."
        )
    )
    parser.add_argument(
        "--python",
        type=Path,
        default=Path(sys.executable),
        help="Python executable used for collection and every isolated pytest child.",
    )
    parser.add_argument(
        "--max-tests-per-process",
        type=int,
        default=MAX_SAFE_TESTS_PER_PROCESS,
        help=f"Normal same-file chunk size (1-{MAX_SAFE_TESTS_PER_PROCESS}).",
    )
    parser.add_argument("--collection-timeout-seconds", type=float, default=600.0)
    parser.add_argument("--batch-timeout-seconds", type=float, default=1800.0)
    parser.add_argument(
        "--output-root",
        type=Path,
        default=REPO_ROOT / "reports" / "validation" / "isolated_pytest",
    )
    parser.add_argument("--keep-passing-logs", action="store_true")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Collect and prove the schedule without executing tests.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    python_executable = args.python.resolve()
    if not python_executable.is_file():
        raise FileNotFoundError(f"Python executable does not exist: {python_executable}")
    if not 1 <= args.max_tests_per_process <= MAX_SAFE_TESTS_PER_PROCESS:
        raise ValueError(
            f"--max-tests-per-process must be between 1 and {MAX_SAFE_TESTS_PER_PROCESS}"
        )

    output_root = args.output_root.resolve()
    run_dir = _run_directory(output_root)
    try:
        nodeids, collection_seconds = collect_nodeids(
            python_executable=python_executable,
            repo_root=REPO_ROOT,
            timeout_seconds=args.collection_timeout_seconds,
        )
    except Exception as exc:
        summary = {
            "schema_version": "PG_ISOLATED_PYTEST_SUMMARY_V1",
            "status": "COLLECTION_FAILED",
            "validation_complete": False,
            "run_dir": str(run_dir),
            "error": f"{type(exc).__name__}: {exc}",
        }
        _write_json(run_dir / "summary.json", summary)
        print(f"Isolated pytest collection failed: {exc}", file=sys.stderr)
        print(f"Structured evidence: {run_dir / 'summary.json'}", file=sys.stderr)
        return 2
    batches = build_schedule(
        nodeids,
        repo_root=REPO_ROOT,
        max_tests_per_process=args.max_tests_per_process,
    )
    manifest = _manifest_payload(
        nodeids=nodeids,
        batches=batches,
        collection_seconds=collection_seconds,
        python_executable=python_executable,
    )
    _write_json(run_dir / "manifest.json", manifest)
    initial_proof = build_coverage_proof(nodeids, batches, verified_nodeids=())

    print(
        f"Collected {len(nodeids)} tests into {len(batches)} sequential process batch(es); "
        f"schedule_exact={initial_proof.schedule_exact}."
    )
    if args.dry_run:
        summary = {
            "schema_version": "PG_ISOLATED_PYTEST_SUMMARY_V1",
            "status": "DRY_RUN",
            "validation_complete": False,
            "run_dir": str(run_dir),
            "coverage": _coverage_payload(initial_proof),
        }
        _write_json(run_dir / "summary.json", summary)
        print(f"Dry-run manifest: {run_dir / 'manifest.json'}")
        return 0

    verified_nodeids: list[str] = []
    results: list[BatchResult] = []
    interrupted = False
    runner_error = ""
    chunks_path = run_dir / "chunks.jsonl"
    try:
        with chunks_path.open("a", encoding="utf-8") as chunk_stream:
            for index, batch in enumerate(batches, start=1):
                result = run_batch(
                    python_executable=python_executable,
                    repo_root=REPO_ROOT,
                    run_dir=run_dir,
                    batch=batch,
                    timeout_seconds=args.batch_timeout_seconds,
                    keep_passing_logs=args.keep_passing_logs,
                )
                results.append(result)
                if result.coverage_verified:
                    verified_nodeids.extend(batch.nodeids)
                _append_json_line(chunk_stream, asdict(result))
                status = "PASS" if result.passed else result.exit_label
                print(
                    f"[{index:04d}/{len(batches):04d}] {status} "
                    f"lane={batch.lane} tests={len(batch.nodeids)} "
                    f"duration={result.duration_seconds:.3f}s"
                )
    except KeyboardInterrupt:
        interrupted = True
    except Exception as exc:
        runner_error = f"{type(exc).__name__}: {exc}"

    proof = build_coverage_proof(nodeids, batches, verified_nodeids=verified_nodeids)
    junit_totals = {
        "tests": sum(result.junit.tests for result in results),
        "failures": sum(result.junit.failures for result in results),
        "errors": sum(result.junit.errors for result in results),
        "skipped": sum(result.junit.skipped for result in results),
    }
    failed_results = [result for result in results if not result.passed]
    native_crashes = [result for result in failed_results if result.native_crash]
    success = proof.execution_exact and not failed_results and not interrupted and not runner_error
    if success:
        status = "PASS"
    elif interrupted:
        status = "INTERRUPTED"
    elif runner_error:
        status = "RUNNER_ERROR"
    else:
        status = "FAIL"
    summary = {
        "schema_version": "PG_ISOLATED_PYTEST_SUMMARY_V1",
        "status": status,
        "validation_complete": not interrupted and not runner_error,
        "run_dir": str(run_dir),
        "coverage": _coverage_payload(proof),
        "junit_totals": junit_totals,
        "failed_batch_count": len(failed_results),
        "native_crash_count": len(native_crashes),
        "runner_error": runner_error,
        "failed_batches": [result.batch_id for result in failed_results],
        "native_crashes": [
            {
                "batch_id": result.batch_id,
                "exit_label": result.exit_label,
                "nodeids": result.nodeids,
                "log_path": result.log_path,
            }
            for result in native_crashes
        ],
    }
    _write_json(run_dir / "summary.json", summary)
    print(
        f"Isolated pytest {status}: "
        f"verified={proof.verified_count}/{proof.collected_count} "
        f"failed_batches={len(failed_results)} native_crashes={len(native_crashes)} "
        f"skipped={junit_totals['skipped']}."
    )
    print(f"Structured evidence: {run_dir / 'summary.json'}")
    if interrupted:
        return 130
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())
