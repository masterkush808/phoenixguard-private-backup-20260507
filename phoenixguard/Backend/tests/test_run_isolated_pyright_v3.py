from __future__ import annotations

from pathlib import Path

import pytest

from Backend.tools.run_isolated_pyright_v3 import (
    bounded_node_options,
    build_batches,
    discover_python_files,
)


def test_discovery_excludes_generated_and_environment_trees(tmp_path: Path) -> None:
    (tmp_path / "Backend" / "src").mkdir(parents=True)
    (tmp_path / "Backend" / "src" / "kept.py").write_text("value = 1\n", encoding="utf-8")
    (tmp_path / "Backend" / "src" / "phoenixguard" / "runtime").mkdir(parents=True)
    (tmp_path / "Backend" / "src" / "phoenixguard" / "runtime" / "kept.py").write_text(
        "value = 2\n",
        encoding="utf-8",
    )
    (tmp_path / "Developer").mkdir()
    (tmp_path / "Developer" / "kept.pyi").write_text("value: int\n", encoding="utf-8")
    for relative in (
        ".venv-dev/Lib/ignored.py",
        ".venv-custom/Lib/ignored.py",
        "runtime/live/ignored.py",
        "reports/ignored.py",
        "Business/web/ignored.py",
        "Backend/src/__pycache__/ignored.py",
    ):
        path = tmp_path.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ignored = True\n", encoding="utf-8")

    assert discover_python_files(tmp_path) == (
        "Backend/src/kept.py",
        "Backend/src/phoenixguard/runtime/kept.py",
        "Developer/kept.pyi",
    )


def test_batches_are_exact_bounded_and_deduplicated() -> None:
    batches = build_batches(("c.py", "a.py", "b.py", "a.py"), 2)

    assert batches == (("a.py", "b.py"), ("c.py",))
    assert [item for batch in batches for item in batch] == ["a.py", "b.py", "c.py"]

    with pytest.raises(ValueError):
        build_batches(("a.py",), 51)


def test_node_heap_option_replaces_unbounded_existing_cap() -> None:
    options = bounded_node_options("--trace-warnings --max-old-space-size=4096", 768)

    assert options == "--trace-warnings --max-old-space-size=768"
