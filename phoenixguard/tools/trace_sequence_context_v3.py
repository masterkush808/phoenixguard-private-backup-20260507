from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping


def _get_json(base_url: str, path: str, timeout: float) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local operator tool
        payload = response.read().decode("utf-8", errors="replace")
    data = json.loads(payload)
    return dict(data) if isinstance(data, Mapping) else {"value": data}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any, fallback: str = "") -> str:
    text = str(value or "").strip()
    return text if text else fallback


def _print_rejections(title: str, rows: list[Any]) -> None:
    print(title)
    if not rows:
        print("  none")
        return
    for row in rows:
        item = _mapping(row)
        print(
            "  - "
            f"{_text(item.get('field'), 'unknown')}: "
            f"received={item.get('received')!r} required={item.get('required')!r} "
            f"module={_text(item.get('failed_module'), 'unknown')}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Trace PhoenixGuard SequenceContextV3 readiness.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8793")
    parser.add_argument("--session", default="pocket-live-8788")
    parser.add_argument("--timeout", type=float, default=8.0)
    parser.add_argument("--json", action="store_true", help="Print the full sequence readiness JSON.")
    args = parser.parse_args(argv)

    session_q = urllib.parse.quote(args.session, safe="")
    try:
        trace = _get_json(args.base_url, f"/v1/mobile/runtime/trace/v3?session_id={session_q}", args.timeout)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"PhoenixGuard SequenceContextV3 trace failed: {exc}", file=sys.stderr)
        return 2

    readiness = _mapping(trace.get("sequence_context_readiness"))
    if not readiness:
        endpoints = _mapping(trace.get("endpoints"))
        readiness = _mapping(_mapping(endpoints.get("sequence_context")).get("payload"))
    if args.json:
        print(json.dumps(readiness, indent=2, sort_keys=True))
        return 0 if readiness.get("ready") else 1

    print("PhoenixGuard SequenceContextV3 Trace")
    print(f"Session: {args.session}")
    print(f"Ready: {bool(readiness.get('ready'))}")
    print(f"Failed module: {_text(readiness.get('failed_module'), 'none')}")
    print(f"Next required: {_text(readiness.get('next_required'), 'none')}")
    print(f"sequence_id: {_text(readiness.get('sequence_id'), 'missing')}")
    print(f"sequence_length: {int(readiness.get('sequence_length') or 0)}")
    print(f"frames_received: {int(readiness.get('frames_received') or 0)}")
    print(f"frames_used: {int(readiness.get('frames_used') or 0)}")
    print(f"frames_dropped: {int(readiness.get('frames_dropped') or 0)}")
    print(f"box_history_len: {int(readiness.get('box_history_len') or 0)}")
    print(f"entry_progression_len: {int(readiness.get('entry_progression_len') or 0)}")
    print(f"progression_len: {int(readiness.get('progression_len') or 0)}")
    print(f"motif_count: {int(readiness.get('motif_count') or 0)}")
    print(f"sequence_signature: {_text(readiness.get('sequence_signature'), 'missing')}")
    print(f"sequence_confidence: {float(readiness.get('sequence_confidence') or 0.0):.4f}")
    print(f"minimum_required_sequence_length: {int(readiness.get('minimum_required_sequence_length') or 0)}")
    print(f"minimum_required_box_history_len: {int(readiness.get('minimum_required_box_history_len') or 0)}")
    print(f"minimum_required_progression_len: {int(readiness.get('minimum_required_progression_len') or 0)}")
    _print_rejections("Missing fields:", _sequence(readiness.get("missing_fields")))
    _print_rejections("Rejected fields:", _sequence(readiness.get("rejected_fields")))
    return 0 if readiness.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
