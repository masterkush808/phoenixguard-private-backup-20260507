from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import ast
import json
import os
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping, cast

from phoenixguard.paths import PACKAGE_ROOT, PROJECT_ROOT

ROOT = PROJECT_ROOT
MANIFEST_PATH = PACKAGE_ROOT / "V3_CANONICAL_MANIFEST.json"
REPORT_DIR = ROOT / "cleanup_reports"

SCAN_SUFFIXES = {".py", ".ps1", ".json", ".md", ".html"}
PRESERVE_PATTERNS = (
    "808_shooter_boxes.json",
    "user_calibration_manifest.json",
    "Backend/config/models/yolov8n.pt",
    "data/",
    "assets/",
)
LEGACY_PATTERNS = {
    "test_signal": re.compile(r"\btest_signal\b|startup_test_entry|--test-signal", re.I),
    "raw_action": re.compile(r"\bexecution_action\b|\bactionable\b|\bSNIPER_READY\b|\bTRIGGER_READY\b", re.I),
    "direct_click": re.compile(r"pyautogui\.click|pyautogui\.moveTo|click_trade_button|click_at\(", re.I),
    "packet": re.compile(r"PG_EXECUTION_PACKET|STUDY_PACKET|packet_id|execution/latest|study/latest|build_.*packet|publish_.*packet", re.I),
    "shooter": re.compile(r"shooter\.py|shooter|execute_trade|signal_loop", re.I),
}


@dataclass(frozen=True)
class FileRecord:
    path: Path
    classification: str
    reason: str


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def load_manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def iter_files() -> list[Path]:
    ignored_parts = {".git", ".venv", "__pycache__", ".pytest_cache", "_archive", "runtime"}
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [
            name
            for name in dirnames
            if name not in ignored_parts and name != ".codex_runtime"
        ]
        current = Path(dirpath)
        for filename in filenames:
            path = current / filename
            if path.suffix.lower() in SCAN_SUFFIXES:
                files.append(path)
    return sorted(files)


def module_to_path(module: str) -> Path | None:
    parts = module.split(".")
    if not parts:
        return None
    base = ROOT
    if parts[0] == "phoenixguard":
        base = ROOT / "Backend" / "src"
    elif parts[0] in {"tests", "tools"}:
        base = ROOT / "Backend"
    candidate = base.joinpath(*parts).with_suffix(".py")
    if candidate.exists():
        return candidate
    package_init = base.joinpath(*parts, "__init__.py")
    if package_init.exists():
        return package_init
    return None


def imports_from_python(path: Path) -> Iterable[Path]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
    except SyntaxError:
        return []
    found: list[Path] = []
    current_module = rel(path).replace("/", ".").removesuffix(".py")
    current_package = current_module.rsplit(".", 1)[0] if "." in current_module else ""
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = module_to_path(alias.name)
                if target is not None:
                    found.append(target)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                package_parts = current_package.split(".") if current_package else []
                base = ".".join(package_parts[: max(0, len(package_parts) - node.level + 1)])
                module = ".".join(part for part in (base, module) if part)
            target = module_to_path(module)
            if target is not None:
                found.append(target)
    return found


def referenced_files_from_text(path: Path, candidates: set[str]) -> Iterable[Path]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    found: list[Path] = []
    for candidate in candidates:
        if candidate in text:
            target = ROOT / candidate
            if target.exists():
                found.append(target)
    return found


def _string_items(value: object) -> list[str]:
    return [str(item) for item in cast(list[object], value)] if isinstance(value, list) and all(isinstance(item, str) for item in cast(list[object], value)) else []


def build_bfs(manifest: dict[str, object], files: list[Path]) -> set[Path]:
    required = [ROOT / item for item in _string_items(manifest.get("required_files"))]
    required += [ROOT / item for item in _string_items(manifest.get("required_tests"))]
    visited: set[Path] = set()
    queue: deque[Path] = deque(path for path in required if path.exists())
    while queue:
        path = queue.popleft().resolve()
        if path in visited:
            continue
        visited.add(path)
        deps: list[Path] = []
        if path.suffix == ".py":
            deps.extend(imports_from_python(path))
        for dep in deps:
            dep = dep.resolve()
            if dep.exists() and dep not in visited:
                queue.append(dep)
    return visited


def classify(path: Path, active: set[Path]) -> FileRecord:
    if not path.exists():
        return FileRecord(path, "STALE_RUNTIME", "Runtime artifact disappeared during scan.")
    rel_path = rel(path)
    normalized = rel_path.lower()
    text = path.read_text(encoding="utf-8", errors="ignore")
    if path.resolve() in active:
        if normalized.startswith("backend/tests/"):
            return FileRecord(path, "ACTIVE_V3_TEST", "Reachable from V3 manifest test list or imports.")
        if any(token in normalized for token in ("model_council_v3", "packet_v3", "floating_state_reducer", "shooter_action_sequencer", "observability_v3")):
            return FileRecord(path, "ACTIVE_V3_CORE", "Reachable from V3 dependency graph and named canonical V3 component.")
        return FileRecord(path, "ACTIVE_V3_SUPPORT", "Reachable from V3 dependency graph.")
    if any(normalized == item or normalized.startswith(item) for item in PRESERVE_PATTERNS):
        return FileRecord(path, "UNKNOWN_REVIEW_REQUIRED", "Preserved artifact/data path; not moved by cleanup tooling.")
    if ".codex_runtime/" in normalized or normalized.startswith("runtime/") or normalized.endswith(".log") or "__pycache__" in normalized:
        return FileRecord(path, "STALE_RUNTIME", "Runtime/cache artifact.")
    if any(pattern.search(text) for pattern in (LEGACY_PATTERNS["test_signal"], LEGACY_PATTERNS["raw_action"], LEGACY_PATTERNS["direct_click"])):
        return FileRecord(path, "LEGACY_REFERENCED", "Contains legacy trigger/click vocabulary but is not moved automatically.")
    if path.name.startswith("test_") and not normalized.startswith("backend/tests/"):
        return FileRecord(path, "LEGACY_UNUSED", "Top-level ad hoc test script outside V3 test suite.")
    if normalized.startswith("docs/") or normalized.startswith("mobile/"):
        return FileRecord(path, "LEGACY_UNUSED", "Documentation/static surface outside active V3 BFS.")
    return FileRecord(path, "UNKNOWN_REVIEW_REQUIRED", "Not reachable from V3 BFS; manual review required before quarantine.")


def write_report(path: Path, title: str, rows: list[str]) -> None:
    path.write_text("# " + title + "\n\n" + "\n".join(rows).rstrip() + "\n", encoding="utf-8")


def make_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    out.extend("| " + " | ".join(cell.replace("\n", " ") for cell in row) + " |" for row in rows)
    return out


def main() -> int:
    manifest = load_manifest()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    files = iter_files()
    active = build_bfs(manifest, files)
    records = [classify(path, active) for path in files]

    graph_rows = [[rel(path), "ACTIVE" if path.resolve() in active else "not reached"] for path in sorted(active)]
    write_report(REPORT_DIR / "v3_dependency_graph.md", "V3 Dependency Graph", make_table(["file", "state"], graph_rows))

    active_rows = [[rel(record.path), record.classification, record.reason] for record in records if record.classification.startswith("ACTIVE_V3")]
    write_report(REPORT_DIR / "v3_active_file_manifest.md", "V3 Active File Manifest", make_table(["file", "classification", "reason"], active_rows))

    legacy_rows = [[rel(record.path), record.classification, record.reason] for record in records if record.classification.startswith("LEGACY") or record.classification == "UNKNOWN_REVIEW_REQUIRED"]
    write_report(REPORT_DIR / "legacy_file_candidates.md", "Legacy File Candidates", make_table(["file", "classification", "reason"], legacy_rows[:500]))

    dirty_rows = [[rel(record.path), record.classification, record.reason] for record in records if record.classification in {"DIRTY_CACHE", "STALE_RUNTIME"}]
    write_report(REPORT_DIR / "dirty_cache_report.md", "Dirty Cache Report", make_table(["file", "classification", "reason"], dirty_rows))

    trigger_rows: list[list[str]] = []
    packet_rows: list[list[str]] = []
    shooter_rows: list[list[str]] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="ignore")
        hits = [name for name, pattern in LEGACY_PATTERNS.items() if pattern.search(text)]
        if not hits:
            continue
        active_state = "yes" if path.resolve() in active else "no"
        row = [rel(path), ", ".join(hits), active_state, "keep" if active_state == "yes" else "review/quarantine"]
        if any(hit in hits for hit in ("test_signal", "raw_action", "direct_click")):
            trigger_rows.append(row)
        if "packet" in hits:
            packet_rows.append(row)
        if "shooter" in hits or "direct_click" in hits:
            shooter_rows.append(row)

    write_report(REPORT_DIR / "legacy_trigger_paths.md", "Legacy Trigger Paths", make_table(["file", "hits", "v3_imports_it", "action"], trigger_rows[:500]))
    write_report(REPORT_DIR / "duplicate_logic_report.md", "Duplicate Logic Report", make_table(["file", "hits", "v3_imports_it", "action"], packet_rows[:500]))
    write_report(REPORT_DIR / "shooter_path_report.md", "Shooter Path Report", make_table(["file", "hits", "v3_imports_it", "action"], shooter_rows[:500]))

    quarantine_rows = [
        [rel(record.path), record.classification, record.reason, "do not move automatically" if record.classification == "UNKNOWN_REVIEW_REQUIRED" else "quarantine after tests"]
        for record in records
        if record.classification in {"LEGACY_UNUSED", "UNKNOWN_REVIEW_REQUIRED"}
    ]
    write_report(REPORT_DIR / "quarantine_plan.md", "Quarantine Plan", make_table(["file", "classification", "reason", "action"], quarantine_rows[:500]))

    manifest_records = [
        {
            "original_path": rel(record.path),
            "classification": record.classification,
            "reason": record.reason,
            "action": "kept" if record.classification.startswith("ACTIVE_V3") else "reported",
        }
        for record in records
    ]
    # Cleanup analysis is disposable report output. Never create or reuse an
    # archive tree; the canonical cleaner removes this report root directly.
    quarantine_manifest_path = REPORT_DIR / "quarantine_manifest.json"
    existing_records: list[dict[str, object]] = []
    if quarantine_manifest_path.exists():
        try:
            existing_payload = json.loads(quarantine_manifest_path.read_text(encoding="utf-8"))
            existing_map = dict(cast(Mapping[str, object], existing_payload)) if isinstance(existing_payload, Mapping) else {}
            raw_records = existing_map.get("records", [])
            if isinstance(raw_records, list):
                for item in cast(list[object], raw_records):
                    item_map = dict(cast(Mapping[str, object], item)) if isinstance(item, Mapping) else {}
                    if item_map.get("new_path"):
                        existing_records.append(item_map)
        except (OSError, ValueError):
            existing_records = []
    merged_records = manifest_records + existing_records
    quarantine_manifest_path.write_text(json.dumps({"records": merged_records}, indent=2, sort_keys=True), encoding="utf-8")
    print(f"Wrote cleanup reports to {REPORT_DIR}")
    print(f"Active files: {len(active_rows)} | legacy/review candidates: {len(legacy_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
