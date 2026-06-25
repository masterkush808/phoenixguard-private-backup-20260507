from __future__ import annotations

import argparse
import os
from dataclasses import asdict, dataclass

from huggingface_hub import HfApi

from phoenixguard.core.config import MODELS


@dataclass
class ModelStatus:
    model: str
    ok: bool
    private_or_gated: bool
    sha: str
    error: str
    network_blocked: bool = False


def _normalize_model_id(model_id: str) -> str:
    if model_id.startswith("hf://"):
        return model_id.replace("hf://", "", 1)
    return model_id


def _is_network_blocked_error(exc: Exception) -> bool:
    text = str(exc).lower()
    indicators = (
        "winerror 10013",
        "winerror 10051",
        "winerror 10060",
        "winerror 11001",
        "errno 11001",
        "getaddrinfo failed",
        "temporary failure in name resolution",
        "name or service not known",
        "failed to establish a new connection",
        "connection refused",
        "connection aborted",
        "network is unreachable",
        "timed out",
        "socket",
    )
    return any(indicator in text for indicator in indicators)


def check_model_access(model_id: str, token: str | None = None) -> ModelStatus:
    api = HfApi(token=token)
    normalized = _normalize_model_id(model_id)
    try:
        info = api.model_info(normalized)
        return ModelStatus(
            model=normalized,
            ok=True,
            private_or_gated=bool(getattr(info, "gated", False) or getattr(info, "private", False)),
            sha=str(getattr(info, "sha", "")),
            error="",
            network_blocked=False,
        )
    except Exception as e:
        return ModelStatus(
            model=normalized,
            ok=False,
            private_or_gated=False,
            sha="",
            error=str(e),
            network_blocked=_is_network_blocked_error(e),
        )


def run_all(token: str | None = None) -> list[ModelStatus]:
    required = [
        MODELS.cv_primary,
        MODELS.cv_fallback,
        MODELS.fin_dora_adapter,
        MODELS.chronos_model,
        MODELS.style_embedder,
    ]
    return [check_model_access(m, token=token) for m in required]


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate HF model accessibility for PhoenixGuard")
    parser.add_argument("--token", default=os.getenv("HF_TOKEN", ""), help="HF token (optional if public models)")
    args = parser.parse_args()

    token = args.token.strip() or None
    results = run_all(token=token)

    failed = False
    print("PhoenixGuard HF Access Report")
    for item in results:
        line = asdict(item)
        print(line)
        if not item.ok:
            failed = True

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
