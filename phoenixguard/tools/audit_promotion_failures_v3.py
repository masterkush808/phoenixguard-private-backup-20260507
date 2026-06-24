from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from typing import Any

from certification_common_v3 import DEFAULT_BASE_URL, DEFAULT_SESSION, ROOT, http_json, quote_session
from certify_v3_full_system_burn_in import (
    BURN_DIR,
    append_jsonl,
    endpoint_payload,
    extract_packet_id,
    mapping,
    promotion_failure_row,
    rank_promotion_blockers,
    render_promotion_failure_report,
    text,
    utc_iso,
    write_json,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit why STUDY_PACKET rows did not promote to PG_EXECUTION_PACKET_V3.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--duration-sec", type=float, default=1.0)
    parser.add_argument("--interval-sec", type=float, default=1.0)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--jsonl-out", default=str(BURN_DIR / "promotion_failures.jsonl"))
    parser.add_argument("--report-out", default=str(ROOT / "reports" / "FINAL_PROMOTION_FAILURE_AUDIT.md"))
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session_q = quote_session(args.session)
    jsonl_path = Path(args.jsonl_out)
    if not jsonl_path.is_absolute():
        jsonl_path = ROOT / jsonl_path
    report_path = Path(args.report_out)
    if not report_path.is_absolute():
        report_path = ROOT / report_path
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    jsonl_path.write_text("", encoding="utf-8")

    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    deadline = time.time() + max(0.1, float(args.duration_sec))
    while time.time() < deadline:
        now = time.time()
        trace_result = http_json(f"{base}/v1/mobile/runtime/trace/v3?session_id={session_q}", timeout=args.timeout)
        trace = mapping(trace_result.payload)
        if not trace_result.ok:
            failures.append(f"runtime trace failed: {trace_result.error or trace_result.status}")
            break
        study = endpoint_payload(trace, "study_latest")
        execution = endpoint_payload(trace, "execution_latest")
        cert_gates = mapping(trace.get("certification_gates"))
        sequence = mapping(trace.get("sequence_context_readiness"))
        sample: dict[str, Any] = {
            "epoch": now,
            "iso": utc_iso(now),
            "source_lock_status": text(mapping(cert_gates.get("source_lock")).get("status"), "MISSING"),
            "packet_contract_status": text(mapping(cert_gates.get("packet_contract")).get("status"), "MISSING"),
            "sequence_ready": bool(sequence.get("ready")),
            "sequence_status": text(sequence.get("sequence_status") or sequence.get("status"), "MISSING"),
            "sequence_length": sequence.get("sequence_length"),
        }
        if extract_packet_id(study) and not extract_packet_id(execution):
            row = promotion_failure_row(now=now, study=study, trace=trace, sample=sample)
            rows.append(row)
            append_jsonl(jsonl_path, row)
        time.sleep(max(0.1, float(args.interval_sec)))

    ranking = rank_promotion_blockers(rows)
    write_json(BURN_DIR / "promotion_blocker_ranking.json", ranking)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_promotion_failure_report(rows, ranking), encoding="utf-8")

    summary: dict[str, Any] = {
        "schema_version": "PG_PROMOTION_FAILURE_AUDIT_V3",
        "session_id": args.session,
        "rows": len(rows),
        "failures": failures,
        "ranking": ranking,
        "jsonl_out": str(jsonl_path),
        "report_out": str(report_path),
    }
    print("PROMOTION_FAILURE_AUDIT: " + ("PASS" if rows or not failures else "FAIL"))
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
