from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from Backend.tools import run_isolated_pytest_v3 as runner


def _write_test_source(root: Path, relative_path: str, source: str) -> None:
    path = root.joinpath(*relative_path.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")


def test_collection_parser_preserves_exact_nodeids_and_duplicates() -> None:
    output = """
Backend/tests/test_alpha.py::test_one
Backend\\tests\\test_alpha.py::test_parameter[value with space]
not a node
Backend/tests/test_alpha.py::test_one

3 tests collected in 0.10s
"""

    assert runner.parse_collected_nodeids(output) == (
        "Backend/tests/test_alpha.py::test_one",
        "Backend\\tests\\test_alpha.py::test_parameter[value with space]",
        "Backend/tests/test_alpha.py::test_one",
    )


def test_schedule_is_exact_bounded_and_isolates_native_and_real_nodes(tmp_path: Path) -> None:
    core_file = "Backend/tests/test_core_sample.py"
    native_file = "Backend/tests/test_native_sample.py"
    real_file = runner.REAL_MODEL_TEST_FILE
    _write_test_source(tmp_path, core_file, "def test_placeholder():\n    pass\n")
    _write_test_source(tmp_path, native_file, "def test_native():\n    import torch\n")
    _write_test_source(tmp_path, real_file, "def test_real():\n    pass\n")

    core_nodes = tuple(f"{core_file}::test_case_{index}" for index in range(53))
    native_nodes = tuple(f"{native_file}::test_case_{index}" for index in range(2))
    real_nodes = tuple(f"{real_file}::test_case_{index}" for index in range(2))
    collected = (*core_nodes, *native_nodes, *real_nodes)

    schedule = runner.build_schedule(collected, repo_root=tmp_path)
    proof = runner.build_coverage_proof(collected, schedule, verified_nodeids=())

    core_batches = [batch for batch in schedule if batch.source_file == core_file]
    native_batches = [batch for batch in schedule if batch.source_file == native_file]
    real_batches = [batch for batch in schedule if batch.source_file == real_file]
    assert [len(batch.nodeids) for batch in core_batches] == [25, 25, 3]
    assert [len(batch.nodeids) for batch in native_batches] == [2]
    assert all(batch.lane == "native" for batch in native_batches)
    assert all(len(batch.nodeids) == 1 and batch.lane == "real_model" for batch in real_batches)
    assert proof.schedule_exact is True
    assert proof.scheduled_count == proof.collected_count == 57
    assert proof.duplicate_scheduled_nodeids == ()


def test_schedule_groups_safe_sentence_transformer_worker_contract_but_isolates_other_real_nodes(
    tmp_path: Path,
) -> None:
    real_file = runner.REAL_MODEL_TEST_FILE
    _write_test_source(tmp_path, real_file, "def test_real():\n    pass\n")
    sentence_nodes = tuple(
        f"{runner.REAL_SENTENCE_TRANSFORMER_NODE_PREFIX}test_contract_{index}"
        for index in range(4)
    )
    other_nodes = (
        f"{real_file}::TestYolo::test_load",
        f"{real_file}::TestChronos::test_forecast",
    )

    schedule = runner.build_schedule((*other_nodes[:1], *sentence_nodes, *other_nodes[1:]), repo_root=tmp_path)
    proof = runner.build_coverage_proof(
        (*other_nodes[:1], *sentence_nodes, *other_nodes[1:]),
        schedule,
        verified_nodeids=(),
    )

    sentence_batches = [
        batch
        for batch in schedule
        if batch.isolation_reason == "isolated_sentence_transformer_worker_contract"
    ]
    singleton_batches = [batch for batch in schedule if batch.isolation_reason == "real_model_test"]
    assert len(sentence_batches) == 1
    assert sentence_batches[0].nodeids == sentence_nodes
    assert all(len(batch.nodeids) == 1 for batch in singleton_batches)
    assert proof.schedule_exact is True


def test_schedule_refuses_unsafe_chunk_size_and_duplicate_collection(tmp_path: Path) -> None:
    source_file = "Backend/tests/test_sample.py"
    _write_test_source(tmp_path, source_file, "def test_one():\n    pass\n")
    node = f"{source_file}::test_one"

    with pytest.raises(ValueError, match="between 1 and 25"):
        runner.build_schedule((node,), repo_root=tmp_path, max_tests_per_process=26)
    with pytest.raises(RuntimeError, match="does not exactly match collection"):
        runner.build_schedule((node, node), repo_root=tmp_path)


def test_environment_caps_threads_and_only_real_lane_enables_provider_access() -> None:
    base = {"PRESERVED": "yes", "OMP_NUM_THREADS": "99"}
    core = runner.build_test_environment(base, lane="core")
    real = runner.build_test_environment(base, lane="real_model")

    assert core["PRESERVED"] == "yes"
    assert core["OMP_NUM_THREADS"] == "1"
    assert core["MKL_NUM_THREADS"] == "1"
    assert core["OPENBLAS_NUM_THREADS"] == "1"
    assert core["CUDA_VISIBLE_DEVICES"] == "-1"
    assert core["HF_HUB_OFFLINE"] == "1"
    assert core["PHOENIXGUARD_ENABLE_CHRONOS_MODEL_IN_TESTS"] == "0"
    assert core["PHOENIXGUARD_BACKGROUND_WARMUP_ON_LAUNCH"] == "0"
    assert real["OMP_NUM_THREADS"] == "1"
    assert real["CUDA_VISIBLE_DEVICES"] == "-1"
    assert real["HF_HUB_OFFLINE"] == "0"
    assert real["TRANSFORMERS_OFFLINE"] == "0"


@pytest.mark.parametrize(
    ("return_code", "expected_label", "native"),
    [
        (0, "PASS", False),
        (1, "PYTEST_EXIT_1", False),
        (0xC0000005, "WINDOWS_NATIVE_ACCESS_VIOLATION_0xC0000005", True),
        (-1073741819, "WINDOWS_NATIVE_ACCESS_VIOLATION_0xC0000005", True),
        (0xC0000017, "WINDOWS_NATIVE_NO_MEMORY_0xC0000017", True),
        (-9, "SIGNAL_9", True),
    ],
)
def test_exit_classification_detects_windows_native_failures(
    return_code: int, expected_label: str, native: bool
) -> None:
    result = runner.classify_process_exit(return_code)
    assert result.label == expected_label
    assert result.native_crash is native


def test_junit_counts_and_execution_proof_require_every_testcase(tmp_path: Path) -> None:
    junit_path = tmp_path / "result.xml"
    junit_path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite tests="3">
  <testcase classname="sample" name="passed" />
  <testcase classname="sample" name="skipped"><skipped /></testcase>
  <testcase classname="sample" name="failed"><failure /></testcase>
</testsuite></testsuites>
""",
        encoding="utf-8",
    )
    counts = runner.parse_junit_counts(junit_path)
    assert counts == runner.JunitCounts(True, 3, 1, 0, 1)

    source_file = "Backend/tests/test_sample.py"
    batch = runner.TestBatch(
        batch_id="batch-0001",
        source_file=source_file,
        nodeids=(f"{source_file}::test_one", f"{source_file}::test_two"),
        lane="core",
        isolation_reason="bounded_same_file_chunk",
    )
    duplicate_batch = replace(batch, batch_id="batch-0002", nodeids=(batch.nodeids[0],))
    proof = runner.build_coverage_proof(batch.nodeids, (batch,), verified_nodeids=batch.nodeids)
    duplicate_proof = runner.build_coverage_proof(
        batch.nodeids, (batch, duplicate_batch), verified_nodeids=batch.nodeids
    )
    assert proof.schedule_exact is True
    assert proof.execution_exact is True
    assert duplicate_proof.schedule_exact is False
    assert duplicate_proof.duplicate_scheduled_nodeids == (batch.nodeids[0],)
