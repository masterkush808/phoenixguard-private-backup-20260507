from __future__ import annotations

import argparse
import json
from typing import Any, Mapping
import urllib.error
import urllib.parse
import urllib.request


def _get_json(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{path}"
    req = urllib.request.Request(url=url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {"_error": f"HTTP {exc.code}", "_url": url}
    except Exception as exc:
        return {"_error": str(exc), "_url": url}
    return payload if isinstance(payload, dict) else {"_error": "non-object response", "_url": url}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _execution(payload: Mapping[str, Any]) -> dict[str, Any]:
    return _mapping(payload.get("execution") or _mapping(payload.get("model_council_packet")).get("execution"))


def _council(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(payload.get("model_council_result"))
    return _mapping(
        payload.get("model_council")
        or result.get("model_council")
        or _mapping(payload.get("model_council_packet")).get("model_council")
    )


def _promotion(payload: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(payload.get("model_council_result"))
    return _mapping(
        payload.get("promotion_trace")
        or result.get("promotion_trace")
        or _mapping(payload.get("model_council_study_packet")).get("promotion_trace")
        or _mapping(payload.get("model_council_packet")).get("promotion_trace")
    )


def _study_packet(payload: Mapping[str, Any]) -> dict[str, Any]:
    for key in ("model_council_study_packet", "study_packet", "latest_model_council_study_packet"):
        value = payload.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    result = _mapping(payload.get("model_council_result"))
    for key in ("model_council_study_packet", "study_packet"):
        value = result.get(key)
        if isinstance(value, Mapping):
            return dict(value)
    return {}


def _lane_context(payload: Mapping[str, Any]) -> dict[str, Any]:
    council = _council(payload)
    promotion = _promotion(payload)
    lane = _mapping(
        payload.get("execution_lane")
        or council.get("execution_lane")
        or promotion.get("execution_lane")
        or _study_packet(payload).get("execution_lane")
    )
    return {
        "name": _first(
            lane.get("name"),
            payload.get("selected_execution_lane"),
            council.get("selected_execution_lane"),
            promotion.get("selected_lane"),
        ),
        "accepted": _first(promotion.get("lane_accepted"), lane.get("accepted")),
        "reason": lane.get("reason"),
        "required_score": lane.get("required_score"),
        "actual_score": lane.get("actual_score"),
    }


def _line(label: str, value: Any) -> str:
    text = "missing" if value in (None, "", [], {}) else str(value)
    return f"  {label}: {text}"


def _print_tracker(payload: Mapping[str, Any]) -> None:
    latest = _mapping(payload.get("latest_signal"))
    council = _council(payload)
    print("Tracker latest:")
    print(_line("status", payload.get("status")))
    print(_line("side", _first(latest.get("execution_action"), latest.get("action"), latest.get("side"), council.get("final_side"))))
    print(_line("state", _first(latest.get("status"), council.get("final_state"))))
    print(_line("reason", _first(latest.get("reason"), latest.get("summary"), council.get("arbitration_reason"))))
    print(_line("trigger", _first(latest.get("trigger"), latest.get("next_event"), latest.get("entry_state"))))


def _print_council(payload: Mapping[str, Any]) -> None:
    council = _council(payload)
    promotion = _promotion(payload)
    lane = _lane_context(payload)
    print("Model Council latest:")
    print(_line("side", _first(council.get("final_side"), promotion.get("candidate_side"))))
    print(_line("state", council.get("final_state")))
    print(_line("reason", _first(payload.get("block_reason"), promotion.get("true_blocker"), council.get("arbitration_reason"), promotion.get("blocked_by"))))
    print(_line("final_score", _first(council.get("final_execution_score"), payload.get("final_execution_score"), promotion.get("final_execution_score"))))
    print(_line("execution_threshold", _first(council.get("execution_threshold"), payload.get("execution_threshold"), promotion.get("execution_threshold"))))
    print(_line("promotion_result", promotion.get("promotion_result")))
    print(_line("true_blocker", promotion.get("true_blocker")))
    print(_line("candidate_id", promotion.get("candidate_id")))
    print(_line("candidate_stage", _first(promotion.get("candidate_stage"), council.get("maturity_stage"))))
    print(_line("selected_lane", lane.get("name")))
    print(_line("lane_accepted", lane.get("accepted")))


def _print_study(payload: Mapping[str, Any]) -> None:
    execution = _execution(payload)
    promotion = _promotion(payload)
    lane = _lane_context(payload)
    print("Study packet latest:")
    print(_line("packet_id", payload.get("packet_id")))
    print(_line("packet_type", payload.get("packet_type")))
    print(_line("state", execution.get("state")))
    print(_line("next_required", promotion.get("next_required")))
    print(_line("selected_lane", lane.get("name")))
    print(_line("lane_accepted", lane.get("accepted")))
    print(_line("lane_reason", lane.get("reason")))


def _print_execution(payload: Mapping[str, Any]) -> None:
    execution = _execution(payload)
    time_sequence = _mapping(execution.get("time_sequence"))
    lane = _lane_context(payload)
    print("Execution latest:")
    print(_line("packet_id", payload.get("packet_id")))
    print(_line("present", not bool(payload.get("_error")) and bool(payload.get("packet_id"))))
    print(_line("state", execution.get("state")))
    print(_line("side", execution.get("side")))
    print(_line("expiry", execution.get("expiry_seconds")))
    print(_line("time_sequence", _first(time_sequence.get("target_text"), time_sequence.get("target_seconds"))))
    print(_line("selected_lane", lane.get("name")))
    print(_line("lane_accepted", lane.get("accepted")))


def _print_shooter(payload: Mapping[str, Any]) -> None:
    print("Shooter handshake:")
    print(_line("packet_seen", payload.get("packet_seen")))
    print(_line("packet_id", payload.get("packet_id")))
    print(_line("packet_type", payload.get("packet_type")))
    print(_line("execution_state", payload.get("execution_state")))
    print(_line("side", payload.get("side")))
    print(_line("reason", payload.get("reason")))
    print(_line("runtime_integrity", payload.get("runtime_integrity")))
    print(_line("gate_1", payload.get("gate_1_second_read")))
    print(_line("gate_2", payload.get("gate_2_trade_discipline")))
    print(_line("gate_3", payload.get("gate_3_model_council")))
    print(_line("calibration", payload.get("calibration")))
    print(_line("true_blocker", payload.get("true_blocker")))
    print(_line("next_required", payload.get("next_required")))
    print(_line("selected_lane", payload.get("selected_execution_lane")))
    print(_line("lane_accepted", payload.get("lane_accepted")))


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare PhoenixGuard V3 tracker, council, study, and execution endpoints.")
    parser.add_argument("--session", "--session-id", dest="session_id", default="pocket-live-8788")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--timeout", type=float, default=2.0)
    args = parser.parse_args()

    session_q = urllib.parse.quote(str(args.session_id))
    payloads = {
        "tracker": _get_json(args.base_url, f"/v1/mobile/window-tracker/sessions/{session_q}", args.timeout),
        "council": _get_json(args.base_url, f"/v1/mobile/model-council/sessions/{session_q}/latest", args.timeout),
        "study": _get_json(args.base_url, f"/v1/mobile/model-council/sessions/{session_q}/study/latest", args.timeout),
        "execution": _get_json(args.base_url, f"/v1/mobile/model-council/sessions/{session_q}/execution/latest", args.timeout),
        "shooter": _get_json(args.base_url, f"/v1/mobile/shooter/sessions/{session_q}/handshake", args.timeout),
    }
    if payloads["study"].get("_error"):
        study_from_council = _study_packet(payloads["council"])
        if study_from_council:
            payloads["study"] = study_from_council
    if payloads["shooter"].get("_error"):
        payloads["shooter"] = _get_json(args.base_url, f"/v1/mobile/shooter/handshake?session_id={session_q}", args.timeout)

    _print_tracker(payloads["tracker"])
    print()
    _print_council(payloads["council"])
    print()
    _print_study(payloads["study"])
    print()
    _print_execution(payloads["execution"])
    print()
    _print_shooter(payloads["shooter"])

    errors = {name: payload["_error"] for name, payload in payloads.items() if payload.get("_error")}
    if errors:
        print()
        print("Endpoint errors:")
        for name, error in errors.items():
            print(f"  {name}: {error}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
