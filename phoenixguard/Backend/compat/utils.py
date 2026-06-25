from __future__ import annotations

from _pg_bootstrap import ensure_project_paths
ensure_project_paths()

from phoenixguard.core.utils import (
    append_hash_chain,
    can_import_chronos_safely,
    can_import_module_safely,
    can_import_sentence_transformers_safely,
    can_import_torchvision_safely,
    clamp,
    safe_json_loads,
    setup_logger,
    sha256_text,
    utc_now_iso,
)

__all__ = [
    "append_hash_chain",
    "can_import_chronos_safely",
    "can_import_module_safely",
    "can_import_sentence_transformers_safely",
    "can_import_torchvision_safely",
    "clamp",
    "safe_json_loads",
    "setup_logger",
    "sha256_text",
    "utc_now_iso",
]
