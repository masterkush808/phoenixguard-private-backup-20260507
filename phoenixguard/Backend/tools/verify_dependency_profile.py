from __future__ import annotations

from _bootstrap import ensure_backend_paths

ensure_backend_paths()

import argparse
from dataclasses import dataclass
import importlib.metadata as metadata
import json
import os
import platform
import sys
from typing import Sequence, cast


@dataclass(frozen=True, slots=True)
class AlternativeRequirement:
    label: str
    choices: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DependencyProfile:
    required: tuple[str, ...]
    alternatives: tuple[AlternativeRequirement, ...] = ()
    optional: tuple[str, ...] = ()
    forbidden: tuple[str, ...] = ()


BASE_REQUIRED = (
    "numpy",
    "pillow",
    "requests",
    "httpx",
    "pydantic",
    "python-dotenv",
)

LIVE_FORBIDDEN = (
    "bitsandbytes",
    "peft",
    "trl",
    "unsloth",
    "tensorflow",
    "tensorflow-cpu",
    "tensorflow-intel",
    "tf-nightly-intel",
    "streamlit",
    "pycaret",
    "langchain",
    "langchain-core",
    "mitmproxy",
    "onnxruntime-gpu",
    "opencv-python",
)

PROFILES: dict[str, DependencyProfile] = {
    "base": DependencyProfile(required=BASE_REQUIRED),
    "live": DependencyProfile(
        required=(
            *BASE_REQUIRED,
            "fastapi",
            "uvicorn",
            "python-multipart",
            "psutil",
            "mss",
            "torch",
            "torchvision",
            "chronos-forecasting",
            "transformers",
            "scikit-learn",
            "scipy",
            "cryptography",
            "pytesseract",
            "keyboard",
        ),
        alternatives=(AlternativeRequirement("cv2 runtime", ("opencv-python-headless", "opencv-python")),),
        optional=("pywin32", "onnxruntime", "mapie"),
        forbidden=LIVE_FORBIDDEN,
    ),
    "decision": DependencyProfile(
        required=(*BASE_REQUIRED, "scikit-learn", "scipy"),
        optional=("mapie", "xgboost"),
    ),
    "vision": DependencyProfile(
        required=(
            *BASE_REQUIRED,
            "opencv-python-headless",
            "torch",
            "torchvision",
            "onnxruntime",
            "ultralytics",
            "mss",
            "pytesseract",
        ),
        forbidden=("onnxruntime-gpu",),
    ),
    "training": DependencyProfile(
        required=(
            *BASE_REQUIRED,
            "opencv-python-headless",
            "torch",
            "torchvision",
            "onnxruntime",
            "ultralytics",
            "accelerate",
            "chronos-forecasting",
            "faiss-cpu",
            "hnswlib",
            "matplotlib",
            "pandas",
            "plotly",
            "safetensors",
            "sentence-transformers",
            "transformers",
            "tqdm",
        ),
        forbidden=("onnxruntime-gpu",),
    ),
    "business": DependencyProfile(
        required=(
            *BASE_REQUIRED,
            "fastapi",
            "uvicorn",
            "python-multipart",
            "email-validator",
            "stripe",
            "cryptography",
        ),
    ),
    "docs-pdf": DependencyProfile(required=("markdown", "reportlab")),
    "dev": DependencyProfile(
        required=(
            *BASE_REQUIRED,
            "fastapi",
            "uvicorn",
            "pytest",
            "pytest-asyncio",
            "pyright",
            "pip-tools",
            "pipdeptree",
            "playwright",
            "pytest-playwright",
            "reportlab",
            "markdown",
        ),
        optional=(
            "mapie",
            "onnxruntime",
            "stripe",
            "email-validator",
            "ruff",
            "mypy",
        ),
    ),
}


def _installed_version(distribution_name: str) -> str:
    try:
        return metadata.version(distribution_name)
    except metadata.PackageNotFoundError:
        return ""


def _installed(distribution_name: str) -> bool:
    return bool(_installed_version(distribution_name))


def _status_rows(names: Sequence[str]) -> list[dict[str, object]]:
    return [
        {
            "name": name,
            "installed": _installed(name),
            "version": _installed_version(name),
        }
        for name in names
    ]


def _single_repo_venv_policy_enabled() -> bool:
    return str(os.getenv("PHOENIXGUARD_SINGLE_REPO_VENV", "1") or "1").strip().lower() not in {
        "0",
        "false",
        "off",
        "no",
    }


def evaluate_profile(profile_name: str) -> dict[str, object]:
    profile = PROFILES[profile_name]
    missing = [name for name in profile.required if not _installed(name)]
    single_repo_venv = _single_repo_venv_policy_enabled()
    forbidden_present = [] if single_repo_venv else [name for name in profile.forbidden if _installed(name)]
    missing_alternatives: list[str] = []
    for alternative in profile.alternatives:
        if not any(_installed(choice) for choice in alternative.choices):
            missing_alternatives.append(f"{alternative.label}: one of {', '.join(alternative.choices)}")
    onnx_cpu = _installed("onnxruntime")
    onnx_gpu = _installed("onnxruntime-gpu")
    variant_failures: list[str] = []
    if onnx_cpu and onnx_gpu:
        variant_failures.append("Do not install onnxruntime and onnxruntime-gpu in the same environment.")
    ok = not missing and not forbidden_present and not missing_alternatives and not variant_failures
    return {
        "ok": ok,
        "profile": profile_name,
        "single_repo_venv": single_repo_venv,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "missing_required": missing,
        "missing_alternatives": missing_alternatives,
        "forbidden_present": forbidden_present,
        "variant_failures": variant_failures,
        "required": _status_rows(profile.required),
        "optional": _status_rows(profile.optional),
        "forbidden": _status_rows(profile.forbidden),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify an active PhoenixGuard dependency profile.")
    parser.add_argument("--profile", choices=tuple(PROFILES), required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = evaluate_profile(str(args.profile))
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        verdict = "PASS" if result["ok"] else "FAIL"
        print(f"DEPENDENCY_PROFILE {args.profile}: {verdict}")
        for key in ("missing_required", "missing_alternatives", "forbidden_present", "variant_failures"):
            values = result[key]
            if isinstance(values, list) and values:
                print(f"{key}:")
                for value in cast(list[object], values):
                    print(f"- {value}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
