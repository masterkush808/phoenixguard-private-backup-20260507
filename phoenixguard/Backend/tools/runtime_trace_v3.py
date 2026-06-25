from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, cast


def _get_json(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local operator tool
        payload = response.read().decode("utf-8", errors="replace")
    data: object = json.loads(payload)
    return dict(cast(Mapping[str, Any], data)) if isinstance(data, Mapping) else {"value": data}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text if text and text.lower() not in {"none", "null", "n/a"} else fallback


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch and summarize PhoenixGuard V3 runtime trace alignment.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--session", default="pocket-live-8788")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--json", action="store_true", help="Print the full trace JSON.")
    args = parser.parse_args(argv)

    session_q = urllib.parse.quote(args.session, safe="")
    try:
        trace = _get_json(args.base_url, f"/v1/mobile/runtime/trace/v3?session_id={session_q}", args.timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"PhoenixGuard V3 runtime trace failed: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(trace, indent=2, sort_keys=True))
        return 0 if _mapping(trace.get("alignment")).get("status") == "PASS" else 1

    nodes = _mapping(trace.get("nodes"))
    endpoints = _mapping(trace.get("endpoints"))
    alignment = _mapping(trace.get("alignment"))
    def endpoint_payload(name: str) -> dict[str, Any]:
        wrapped = _mapping(endpoints.get(name))
        if "status" in wrapped and str(wrapped.get("status") or "").strip().upper() != "PASS":
            return _mapping(nodes.get(name))
        return _mapping(wrapped.get("payload")) or _mapping(nodes.get(name))

    floating = endpoint_payload("floating_state")
    floating_council = _mapping(floating.get("council"))
    floating_instrument = _mapping(floating.get("instrument"))
    floating_shooter = _mapping(floating.get("shooter"))
    study = endpoint_payload("study_latest")
    execution = endpoint_payload("execution_latest")
    health = endpoint_payload("model_health")
    floating_health = _mapping(floating.get("health"))
    if not health or not (health.get("models_awake") or health.get("models_total")):
        health = floating_health
    sequence_readiness = _mapping(trace.get("sequence_context_readiness"))
    if not sequence_readiness:
        sequence_readiness = _mapping(_mapping(endpoint_payload("sequence_context")).get("payload"))
    dataflow = _mapping(trace.get("dataflow_contract_trace"))
    dataflow_nodes = _mapping(dataflow.get("nodes"))

    print("PhoenixGuard V3 Runtime Trace")
    print(f"Session: {args.session}")
    print(f"Alignment: {_text(alignment.get('status'), 'UNKNOWN')}")
    print(f"Study packet: {_text(study.get('packet_id'), 'not published')}")
    print(f"Execution packet: {_text(execution.get('packet_id'), 'not published')}")
    print(f"Council: {_text(floating_council.get('state'), 'UNKNOWN')} | side={_text(floating_council.get('side'), 'pending')} | lane={_text(floating_council.get('lane_short'), 'pending')}")
    print(f"Instrument: {_text(floating_instrument.get('state'), 'UNKNOWN')} | broker_click_safe={bool(floating_instrument.get('broker_click_safe'))}")
    print(f"Next required: {_text(floating_council.get('next_required') or floating_instrument.get('next_required'), 'none')}")
    if sequence_readiness:
        print(
            "SequenceContextV3: "
            f"ready={bool(sequence_readiness.get('ready'))} "
            f"status={_text(sequence_readiness.get('sequence_status'), 'MISSING')} "
            f"length={int(sequence_readiness.get('sequence_length') or 0)}/"
            f"{int(sequence_readiness.get('minimum_required_sequence_length') or 0)} "
            f"frames={int(sequence_readiness.get('frames_used') or 0)}/"
            f"{int(sequence_readiness.get('frames_received') or 0)} "
            f"box_history={int(sequence_readiness.get('box_history_len') or 0)} "
            f"progression={int(sequence_readiness.get('progression_len') or 0)} "
            f"entry_progression={int(sequence_readiness.get('entry_progression_len') or 0)} "
            f"failed_module={_text(sequence_readiness.get('failed_module'), 'none')}"
        )
        print(f"Sequence next: {_text(sequence_readiness.get('next_required'), 'none')}")
    if dataflow_nodes:
        print(
            "Dataflow: "
            f"source={_text(dataflow_nodes.get('BrokerSourceLockV3'), 'UNKNOWN')} "
            f"tracker={_text(dataflow_nodes.get('LatestFrameBufferV3'), 'UNKNOWN')} "
            f"sequence={_text(dataflow_nodes.get('SequenceContextV3'), 'UNKNOWN')} "
            f"council={_text(dataflow_nodes.get('ModelCouncilV3'), 'UNKNOWN')} "
            f"study={_text(dataflow_nodes.get('STUDY_PACKET'), 'UNKNOWN')} "
            f"execution={_text(dataflow_nodes.get('PG_EXECUTION_PACKET_V3'), 'UNKNOWN')} "
            f"shooter={_text(dataflow_nodes.get('ShooterPackageReporter'), 'UNKNOWN')}"
        )
        print(f"Dataflow reason: {_text(dataflow.get('reason'), 'none')}")
    print(f"Shooter: {_text(floating_shooter.get('state'), 'UNKNOWN')} | {_text(floating_shooter.get('action'), 'waiting')}")
    print(f"Models: {int(health.get('models_awake') or 0)}/{int(health.get('models_total') or 0)}")
    return 0 if alignment.get("status") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
