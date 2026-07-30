from __future__ import annotations

from pathlib import Path
import sys

import pytest

from Backend.tools import clean_v3_runtime_state as cleaner


def _configure_cleaner(monkeypatch: pytest.MonkeyPatch, root: Path) -> Path:
    runtime_dir = (root / "runtime" / "live").resolve()
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(cleaner, "ROOT", root)
    monkeypatch.setattr(cleaner, "ROOT_RESOLVED", root.resolve())
    monkeypatch.setattr(cleaner, "EXPECTED_RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(cleaner, "RUNTIME_DIR", runtime_dir)
    monkeypatch.setattr(cleaner, "LEGACY_ARCHIVE_DIR", root / "_archive")
    monkeypatch.setattr(cleaner, "LEGACY_RUNTIME_BACKUP_DIR", (root / "_archive" / "runtime_backup").resolve())
    return runtime_dir


def test_cleanup_dry_run_has_zero_filesystem_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _configure_cleaner(monkeypatch, tmp_path)
    disposable = runtime_dir / "old-session.json"
    disposable.write_text("generated", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["clean_v3_runtime_state.py"])

    assert cleaner.main() == 0
    assert disposable.exists()
    assert not (tmp_path / "_archive").exists()


def test_cleanup_apply_deletes_disposable_state_and_preserves_operator_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime_dir = _configure_cleaner(monkeypatch, tmp_path)
    disposable = runtime_dir / "data_live"
    disposable.mkdir()
    (disposable / "derived-frame.jpg").write_bytes(b"derived")
    preserved = runtime_dir / "floating_window_v2.json"
    preserved.write_text("{}", encoding="utf-8")
    old_backup = tmp_path / "_archive" / "runtime_backup" / "20260719_010101"
    old_backup.mkdir(parents=True)
    (old_backup / "old-runtime.json").write_text("generated", encoding="utf-8")
    reports = tmp_path / "reports"
    reports.mkdir()
    blueprint = reports / "architecture-blueprint.pdf"
    blueprint.write_bytes(b"committed documentation")
    cleanup_reports = tmp_path / "cleanup_reports"
    cleanup_reports.mkdir()
    (cleanup_reports / "old-validation.json").write_text("generated", encoding="utf-8")
    codex_runtime = tmp_path / ".codex_runtime"
    codex_runtime.mkdir()
    (codex_runtime / "old-state.json").write_text("generated", encoding="utf-8")
    web = tmp_path / "Business" / "web"
    (web / "test-results").mkdir(parents=True)
    (web / "test-results" / ".last-run.json").write_text("{}", encoding="utf-8")
    (web / "reports").mkdir()
    smoke_image = web / "reports" / "product_dashboard_source_console_smoke.png"
    smoke_image.write_bytes(b"generated")
    build_info = web / "tsconfig.tsbuildinfo"
    build_info.write_text("generated", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["clean_v3_runtime_state.py", "--apply"])

    assert cleaner.main() == 0
    assert not disposable.exists()
    assert not (tmp_path / "_archive" / "runtime_backup").exists()
    assert reports.exists()
    assert blueprint.exists()
    assert not cleanup_reports.exists()
    assert not codex_runtime.exists()
    assert not (web / "test-results").exists()
    assert not smoke_image.exists()
    assert not build_info.exists()
    assert preserved.exists()
    assert not (tmp_path / "_archive").exists()


def test_cleanup_skips_virtualenv_bytecode_but_removes_source_bytecode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_cleaner(monkeypatch, tmp_path)
    virtualenv_cache = tmp_path / ".venv-live" / "Lib" / "site-packages" / "pkg" / "__pycache__"
    virtualenv_cache.mkdir(parents=True)
    virtualenv_bytecode = virtualenv_cache / "module.cpython-311.pyc"
    virtualenv_bytecode.write_bytes(b"required environment cache")
    (tmp_path / ".venv-live" / "pyvenv.cfg").write_text("home = python", encoding="utf-8")

    source_cache = tmp_path / "Backend" / "src" / "phoenixguard" / "runtime" / "__pycache__"
    source_cache.mkdir(parents=True)
    source_bytecode = source_cache / "module.cpython-311.pyc"
    source_bytecode.write_bytes(b"disposable source cache")
    monkeypatch.setattr(sys, "argv", ["clean_v3_runtime_state.py", "--apply"])

    assert cleaner.main() == 0
    assert virtualenv_bytecode.exists()
    assert not source_cache.exists()


def test_cleanup_rejects_reparse_redirect_for_disposable_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_cleaner(monkeypatch, tmp_path)
    cleanup_reports = tmp_path / "cleanup_reports"
    cleanup_reports.mkdir()
    protected = tmp_path / "data"
    protected.mkdir()
    (protected / "keep.json").write_text("{}", encoding="utf-8")
    original_reparse_check = cleaner._is_reparse_point  # pyright: ignore[reportPrivateUsage]

    def fake_reparse(path: Path) -> bool:
        return path == cleanup_reports or original_reparse_check(path)

    monkeypatch.setattr(cleaner, "_is_reparse_point", fake_reparse)

    with pytest.raises(RuntimeError, match="symlink or junction"):
        cleaner.collect_runtime_paths()
    assert (protected / "keep.json").exists()


def test_cleanup_refuses_a_tree_with_a_nested_reparse_descendant(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configure_cleaner(monkeypatch, tmp_path)
    cleanup_reports = tmp_path / "cleanup_reports"
    nested_redirect = cleanup_reports / "nested-junction"
    nested_redirect.mkdir(parents=True)
    protected_file = nested_redirect / "keep.json"
    protected_file.write_text("{}", encoding="utf-8")
    original_reparse_check = cleaner._is_reparse_point  # pyright: ignore[reportPrivateUsage]

    def fake_reparse(path: Path) -> bool:
        return path == nested_redirect or original_reparse_check(path)

    monkeypatch.setattr(cleaner, "_is_reparse_point", fake_reparse)
    monkeypatch.setattr(sys, "argv", ["clean_v3_runtime_state.py", "--apply"])

    with pytest.raises(RuntimeError, match="containing a symlink or junction"):
        cleaner.main()
    assert protected_file.exists()


def test_cleanup_report_generator_never_creates_archive_root() -> None:
    source = (
        Path(__file__).resolve().parents[2]
        / "Backend"
        / "tools"
        / "generate_v3_cleanup_reports.py"
    ).read_text(encoding="utf-8")

    assert 'ROOT / "_archive"' not in source
    assert 'REPORT_DIR / "quarantine_manifest.json"' in source


def test_canonical_launcher_discards_unbounded_child_stdio() -> None:
    project_root = Path(__file__).resolve().parents[2]
    live_ready = (project_root / "Backend" / "launch" / "launch_phoenixguard_live_ready.ps1").read_text(
        encoding="utf-8"
    )
    full_local = (project_root / "Backend" / "launch" / "start_phoenixguard_full_local.ps1").read_text(
        encoding="utf-8"
    )

    assert "$env:PHOENIXGUARD_PERSIST_CHILD_STDIO = '0'" in live_ready
    assert "$env:PHOENIXGUARD_DISK_GUARD_INCLUDE_CODEX_SESSIONS = '0'" in live_ready
    assert "$guardOutPath = 'NUL'" in live_ready
    assert "$persistChildStdio = ($env:PHOENIXGUARD_PERSIST_CHILD_STDIO -eq '1')" in full_local
    assert "$discardStdoutPath = 'NUL'" in full_local
    assert "$discardStderrPath = '\\\\.\\NUL'" in full_local
