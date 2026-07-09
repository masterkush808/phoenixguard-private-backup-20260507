from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Iterable, Mapping, TypedDict, cast


DEFAULT_PATTERNS = (
    "models/**/*",
    "memory_bank/**/*",
    "adapters/**/*",
    ".hf_cache/**/*",
    "*.pt",
    "data/*.bin",
)


class AssetEntry(TypedDict):
    path: str
    bytes: int
    sha256: str


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_asset_files(root: Path, patterns: Iterable[str]) -> list[Path]:
    files: dict[str, Path] = {}
    for pattern in patterns:
        for path in root.glob(pattern):
            if path.is_file():
                files[str(path.relative_to(root).as_posix())] = path
    return [files[key] for key in sorted(files)]


def _object_mapping(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    mapping = cast(Mapping[object, object], value)
    return {str(key): item for key, item in mapping.items()}


def _int_from_object(value: object, default: int = 0) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, float | str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


def build_manifest(root: Path, patterns: Iterable[str]) -> dict[str, object]:
    files: list[AssetEntry] = []
    for path in _iter_asset_files(root, patterns):
        stat = path.stat()
        files.append(
            {
                "path": path.relative_to(root).as_posix(),
                "bytes": int(stat.st_size),
                "sha256": _sha256(path),
            }
        )
    return {
        "schema_version": "PG_MODEL_ASSET_MANIFEST_V1",
        "root": str(root),
        "file_count": len(files),
        "files": files,
    }


def verify_manifest(root: Path, manifest_path: Path) -> dict[str, object]:
    decoded: object = json.loads(manifest_path.read_text(encoding="utf-8"))
    parsed: dict[str, object] = _object_mapping(decoded)
    raw_files: object = parsed.get("files", [])
    files_obj = cast(list[object], raw_files) if isinstance(raw_files, list) else []
    failures: list[dict[str, object]] = []
    for item in files_obj:
        if not isinstance(item, Mapping):
            continue
        item_mapping = _object_mapping(cast(object, item))
        rel = str(item_mapping.get("path") or "")
        expected_hash = str(item_mapping.get("sha256") or "")
        expected_bytes = _int_from_object(item_mapping.get("bytes"), 0)
        path = root / rel
        if not path.exists():
            failures.append({"path": rel, "reason": "missing"})
            continue
        actual_bytes = path.stat().st_size
        if expected_bytes and actual_bytes != expected_bytes:
            failures.append({"path": rel, "reason": "byte_count_mismatch", "expected": expected_bytes, "actual": actual_bytes})
            continue
        actual_hash = _sha256(path)
        if actual_hash != expected_hash:
            failures.append({"path": rel, "reason": "sha256_mismatch", "expected": expected_hash, "actual": actual_hash})
    return {
        "schema_version": "PG_MODEL_ASSET_VERIFY_V1",
        "ok": not failures,
        "checked": len(files_obj),
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate or verify PhoenixGuard model/runtime asset hashes.")
    parser.add_argument("--root", default=".", help="PhoenixGuard repository root.")
    parser.add_argument("--manifest", required=True, help="Manifest path to write or verify.")
    parser.add_argument("--mode", choices=("generate", "verify"), default="verify")
    parser.add_argument("--pattern", action="append", default=[], help="Glob pattern relative to --root. May repeat.")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    manifest_path = Path(args.manifest).expanduser()
    if args.mode == "generate":
        manifest = build_manifest(root, args.pattern or DEFAULT_PATTERNS)
        manifest_path.parent.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        print(json.dumps({"ok": True, "manifest": str(manifest_path), "file_count": manifest["file_count"]}, indent=2))
        return 0
    report = verify_manifest(root, manifest_path)
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if bool(report["ok"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
