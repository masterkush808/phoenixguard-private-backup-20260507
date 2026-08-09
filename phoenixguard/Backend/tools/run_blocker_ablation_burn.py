from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Mapping


BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = BACKEND_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from phoenixguard.decision.blocker_ablation_profile_v3 import (  # noqa: E402
    BlockerAblationProfile,
    build_shadow_allowance_package_v1,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_mapping(row) for row in value if isinstance(row, Mapping)]


def _load_records(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".jsonl":
        return [
            _mapping(json.loads(line))
            for line in text.splitlines()
            if line.strip()
        ]
    payload = json.loads(text)
    if isinstance(payload, list):
        return [_mapping(row) for row in payload]
    root = _mapping(payload)
    for key in ("records", "results", "frames", "packets"):
        if isinstance(root.get(key), list):
            return [_mapping(row) for row in root[key]]
    return [root]


def _extract_inputs(record: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    council = _mapping(record.get("model_council"))
    opportunity = _mapping(
        record.get("opportunity_maturity") or council.get("opportunity_maturity")
    )
    book_strategy = _mapping(record.get("book_strategy") or council.get("book_strategy"))
    allowance = _mapping(
        record.get("allowance_package") or council.get("allowance_package")
    )
    blockers = [
        *_rows(opportunity.get("blockers")),
        *_rows(book_strategy.get("blockers")),
    ]
    warnings = [
        *_rows(opportunity.get("soft_contributors")),
        *_rows(book_strategy.get("soft_warnings")),
    ]
    true_blocker = str(
        record.get("true_blocker")
        or council.get("true_blocker")
        or allowance.get("true_blocker")
        or ""
    ).strip().upper()
    if true_blocker not in {"", "NONE", "NULL"}:
        blockers.append(
            {
                "code": true_blocker,
                "reason": record.get("next_required") or council.get("next_required") or "",
                "source": "burn_input",
            }
        )
    return allowance, blockers, warnings


def _profiles(value: str) -> tuple[BlockerAblationProfile, ...]:
    token = str(value or "ALL").strip().upper()
    if token == "ALL":
        return tuple(BlockerAblationProfile)
    return tuple(
        BlockerAblationProfile(item.strip().upper())
        for item in token.split(",")
        if item.strip()
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run offline blocker-profile burns against saved Model Council JSON/JSONL records."
    )
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--profiles", default="ALL")
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--summary-output", type=Path)
    args = parser.parse_args()

    records = _load_records(args.input)
    if args.limit > 0:
        records = records[: args.limit]
    selected_profiles = _profiles(args.profiles)
    output_rows: list[dict[str, Any]] = []
    summary: dict[str, dict[str, int]] = {
        profile.value: {
            "records": 0,
            "would_allow": 0,
            "prepare": 0,
            "blocked": 0,
            "no_direction": 0,
        }
        for profile in selected_profiles
    }
    for index, record in enumerate(records):
        allowance, blockers, warnings = _extract_inputs(record)
        for profile in selected_profiles:
            shadow = build_shadow_allowance_package_v1(
                allowance,
                blockers=blockers,
                warnings=warnings,
                profile=profile,
            )
            output_rows.append(
                {
                    "source_index": index,
                    "packet_id": record.get("packet_id") or allowance.get("packet_id"),
                    "ablation_profile": profile.value,
                    "shadow_allowance_package": shadow,
                }
            )
            counters = summary[profile.value]
            counters["records"] += 1
            state = str(shadow.get("shadow_state") or "NO_DIRECTION").lower()
            if state == "would_allow":
                counters["would_allow"] += 1
            elif state == "prepare":
                counters["prepare"] += 1
            elif state == "blocked":
                counters["blocked"] += 1
            else:
                counters["no_direction"] += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in output_rows),
        encoding="utf-8",
    )
    summary_path = args.summary_output or args.output.with_suffix(".summary.json")
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "schema_version": "PG_BLOCKER_ABLATION_BURN_SUMMARY_V1",
                "input": str(args.input),
                "output": str(args.output),
                "profile_summary": summary,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
