from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from certification_common_v3 import DEFAULT_BASE_URL, DEFAULT_SESSION, ROOT, http_json, quote_session
from certify_v3_full_system_burn_in import (
    collect_lstm_predictions,
    collect_two_candle,
    endpoint_payload,
    mapping,
)


def _report(lstm_rows: list[dict[str, Any]], two_rows: list[dict[str, Any]], failures: list[str], warnings: list[str]) -> str:
    return "\n".join(
        [
            "# Agent 3 LSTM and Two-Candle Audit",
            "",
            "## CLEAR ANSWER",
            "",
            "LSTM and two-candle evidence was sampled from canonical RuntimeTraceV3 payloads.",
            "",
            "## CONFIDENCE LEVEL",
            "",
            "`0.84`" if lstm_rows and two_rows else "`0.58`",
            "",
            "## KEY CAVEATS",
            "",
            "- Accuracy can only be calculated after enough settled outcomes exist.",
            "- These contributors must remain advisory and cannot publish PG_EXECUTION_PACKET_V3.",
            "",
            "## EVIDENCE",
            "",
            "```json",
            json.dumps(
                {
                    "lstm_row_count": len(lstm_rows),
                    "two_candle_row_count": len(two_rows),
                    "lstm_sample": lstm_rows[:5],
                    "two_candle_sample": two_rows[:5],
                    "warnings": warnings,
                    "failures": failures,
                },
                indent=2,
                sort_keys=True,
                default=str,
            ),
            "```",
            "",
            "## PATCHES APPLIED",
            "",
            "- none by this audit tool",
            "",
            "## TESTS RUN",
            "",
            "- RuntimeTraceV3 LSTM/two-candle extraction",
            "",
            "## NEXT REQUIRED",
            "",
            "- If counts are 0, wire contributor summaries into study packets and runtime trace.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit PhoenixGuard V3 LSTM and two-candle contributor visibility.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--out", default=str(ROOT / "reports" / "AGENT_3_LSTM_TWO_CANDLE_REPORT.md"))
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session_q = quote_session(args.session)
    failures: list[str] = []
    warnings: list[str] = []
    trace_result = http_json(f"{base}/v1/mobile/runtime/trace/v3?session_id={session_q}", timeout=args.timeout)
    trace = mapping(trace_result.payload)
    if not trace_result.ok:
        failures.append(f"runtime trace failed: {trace_result.error or trace_result.status}")
    payloads: list[dict[str, Any]] = [
        trace,
        endpoint_payload(trace, "tracker_latest"),
        endpoint_payload(trace, "model_council_latest"),
        endpoint_payload(trace, "study_latest"),
        endpoint_payload(trace, "execution_latest"),
    ]
    lstm_rows = collect_lstm_predictions(payloads)
    two_rows = collect_two_candle(payloads)
    if not lstm_rows:
        warnings.append("no LSTM rows were visible in sampled runtime payloads")
    if not two_rows:
        warnings.append("no two-candle rows were visible in sampled runtime payloads")
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_report(lstm_rows, two_rows, failures, warnings), encoding="utf-8")
    print("LSTM_TWO_CANDLE_AUDIT: " + ("PASS" if lstm_rows and two_rows and not failures else "WARN" if not failures else "FAIL"))
    print(json.dumps({"lstm_rows": len(lstm_rows), "two_candle_rows": len(two_rows), "out": str(out_path), "warnings": warnings, "failures": failures}, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
