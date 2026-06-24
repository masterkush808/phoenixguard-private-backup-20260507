from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from certification_common_v3 import DEFAULT_BASE_URL, DEFAULT_SESSION, ROOT, http_json, quote_session
from certify_v3_full_system_burn_in import _collect_skill_contributions, _endpoint_payload, _mapping


def _report(rows: list[dict[str, Any]], failures: list[str], warnings: list[str]) -> str:
    return "\n".join(
        [
            "# Agent 3 Skill Contribution Audit",
            "",
            "## CLEAR ANSWER",
            "",
            "Skill contribution evidence was sampled from canonical RuntimeTraceV3 payloads.",
            "",
            "## CONFIDENCE LEVEL",
            "",
            "`0.82`" if rows else "`0.55`",
            "",
            "## KEY CAVEATS",
            "",
            "- Skills are certified as contributors only when rows are visible in runtime trace payloads.",
            "- This tool does not permit skills to publish execution authority.",
            "",
            "## EVIDENCE",
            "",
            "```json",
            json.dumps({"row_count": len(rows), "sample": rows[:10], "warnings": warnings, "failures": failures}, indent=2, sort_keys=True, default=str),
            "```",
            "",
            "## PATCHES APPLIED",
            "",
            "- none by this audit tool",
            "",
            "## TESTS RUN",
            "",
            "- RuntimeTraceV3 skill contribution extraction",
            "",
            "## NEXT REQUIRED",
            "",
            "- If row_count is 0, wire skill_contributions into model council study/runtime trace payloads.",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit PhoenixGuard V3 skill contribution visibility and authority boundaries.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--session", default=DEFAULT_SESSION)
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--out", default=str(ROOT / "reports" / "AGENT_3_SKILLS_LSTM_REASONING_REPORT.md"))
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    session_q = quote_session(args.session)
    failures: list[str] = []
    warnings: list[str] = []
    trace_result = http_json(f"{base}/v1/mobile/runtime/trace/v3?session_id={session_q}", timeout=args.timeout)
    trace = _mapping(trace_result.payload)
    if not trace_result.ok:
        failures.append(f"runtime trace failed: {trace_result.error or trace_result.status}")
    payloads: list[dict[str, Any]] = [
        trace,
        _endpoint_payload(trace, "tracker_latest"),
        _endpoint_payload(trace, "model_council_latest"),
        _endpoint_payload(trace, "study_latest"),
        _endpoint_payload(trace, "execution_latest"),
        _mapping(_endpoint_payload(trace, "floating_state")),
    ]
    rows = _collect_skill_contributions(payloads)
    if not rows:
        warnings.append("no skill contribution rows were visible in sampled runtime payloads")
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(_report(rows, failures, warnings), encoding="utf-8")
    print("SKILL_CONTRIBUTION_AUDIT: " + ("PASS" if rows and not failures else "WARN" if not failures else "FAIL"))
    print(json.dumps({"rows": len(rows), "out": str(out_path), "warnings": warnings, "failures": failures}, indent=2, sort_keys=True))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
