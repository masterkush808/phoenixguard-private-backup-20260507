from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys
from typing import Any, Mapping, Sequence, cast
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "phoenixguard" / "V3_CANONICAL_MANIFEST.json"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REQUIRED_IMPORTS = {
    "Tracker API": "phoenixguard.mobile_api.app",
    "Model Council V3": "phoenixguard.decision.model_council_v3",
    "Market Reality Engine": "phoenixguard.decision.market_reality_engine",
    "Execution Packet V3": "phoenixguard.execution.packet_v3",
    "V3 Language Contracts": "phoenixguard.execution.v3_language",
    "Shooter Action Sequencer": "phoenixguard.execution.shooter_action_sequencer",
    "Floating State V2": "phoenixguard.execution.floating_state_reducer",
    "Calibration Manifest": "phoenixguard.execution.calibration_manifest",
    "Observability V3": "phoenixguard.runtime.observability_v3",
}
REQUIRED_BOX_TARGETS = {
    "broker_screen",
    "time_button",
    "hourly_input",
    "minute_input",
    "buy_icon",
    "sell_icon",
    "final_screen",
}
BENIGN_RUNTIME_FILES = {
    "floating_window_v2.json",
    "tracker_launcher_stdout.log",
    "tracker_launcher_stderr.log",
    "tracker_status.json",
}
BENIGN_RUNTIME_DIRS_IF_EMPTY = {
    "data_live",
    "frontend_heartbeat_v3",
    "logs_live",
    "overlay_geometry_dumps",
    "overlay_persist_logs",
}


def status_line(name: str, ok: bool, detail: str = "") -> str:
    return f"{name}: {'PASS' if ok else 'FAIL'}" + (f" - {detail}" if detail else "")


def load_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return dict(cast(Mapping[str, object], payload)) if isinstance(payload, Mapping) else {}


def mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def sequence(value: object) -> list[object]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(cast(Sequence[object], value))
    return []


def runtime_entry_is_active(path: Path) -> bool:
    if path.name in BENIGN_RUNTIME_FILES:
        return False
    if path.is_dir() and path.name in BENIGN_RUNTIME_DIRS_IF_EMPTY:
        try:
            return any(path.iterdir())
        except OSError:
            return True
    return True


def endpoint_ok(base_url: str, path: str, timeout: float) -> tuple[bool, str]:
    url = base_url.rstrip("/") + path
    try:
        with urllib.request.urlopen(urllib.request.Request(url, method="GET"), timeout=timeout) as resp:
            if 200 <= int(resp.status) < 300:
                return True, str(resp.status)
            return False, str(resp.status)
    except urllib.error.HTTPError as exc:
        if exc.code == 404 and "execution/latest" in path:
            return True, "404 acceptable when no executable packet is published"
        return False, f"HTTP {exc.code}"
    except Exception as exc:
        return False, str(exc)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify PhoenixGuard V3 active-only integrity.")
    parser.add_argument("--base-url", default="", help="Optional running API base URL for endpoint checks.")
    parser.add_argument("--session", default="pocket-live-8788")
    parser.add_argument("--timeout", type=float, default=3.0)
    args = parser.parse_args()

    failures: list[str] = []
    print("PhoenixGuard V3 Integrity Check\n")

    manifest_ok = MANIFEST_PATH.exists()
    print(status_line("V3 manifest", manifest_ok, MANIFEST_PATH.relative_to(ROOT).as_posix()))
    if not manifest_ok:
        return 1
    manifest = load_json(MANIFEST_PATH)
    print(f"Active Version: {manifest.get('active_version')}")

    for raw in sequence(manifest.get("required_files", [])):
        path = ROOT / str(raw)
        ok = path.exists()
        print(status_line(f"Required file {raw}", ok))
        if not ok:
            failures.append(str(raw))

    for label, module_name in REQUIRED_IMPORTS.items():
        try:
            importlib.import_module(module_name)
            print(status_line(label, True))
        except Exception as exc:
            print(status_line(label, False, str(exc)))
            failures.append(label)

    boxes_path = ROOT / "808_shooter_boxes.json"
    boxes_ok = boxes_path.exists()
    missing_targets = sorted(REQUIRED_BOX_TARGETS)
    if boxes_ok:
        try:
            boxes = load_json(boxes_path)
            points = mapping(boxes.get("points"))
            if points:
                available = set(points)
            else:
                available = set(boxes.keys())
            missing_targets = sorted(REQUIRED_BOX_TARGETS - available)
        except Exception as exc:
            missing_targets = sorted(REQUIRED_BOX_TARGETS)
            print(status_line("Calibration JSON", False, str(exc)))
    calibration_ok = boxes_ok and not missing_targets and (ROOT / "user_calibration_manifest.json").exists()
    print(status_line("Calibration", calibration_ok, "" if calibration_ok else f"missing {missing_targets}"))
    if not calibration_ok:
        failures.append("Calibration")

    shooter_text = (ROOT / "shooter.py").read_text(encoding="utf-8", errors="ignore")
    v3_guard_ok = "Live execution authority: PG_EXECUTION_PACKET_V3" in shooter_text and "production V3 modes require PG_EXECUTION_PACKET_V3 only" in shooter_text
    print(status_line("Legacy Trigger Paths", v3_guard_ok, "DISABLED" if v3_guard_ok else "guard text missing"))
    if not v3_guard_ok:
        failures.append("Legacy Trigger Paths")

    launch_profile = mapping(manifest.get("launch_profile"))
    canonical_launcher = ROOT / str(launch_profile.get("launcher") or "")
    engine_launcher = ROOT / str(launch_profile.get("engine_launcher") or "")
    launcher_text = canonical_launcher.read_text(encoding="utf-8", errors="ignore") if canonical_launcher.exists() else ""
    engine_launcher_text = engine_launcher.read_text(encoding="utf-8", errors="ignore") if engine_launcher.exists() else ""
    profile_ok = (
        launch_profile.get("production") == "FINAL_LIVE"
        and launch_profile.get("shooter_mode") == "LIVE_READY"
        and launch_profile.get("live_click_arm") == "set_by_canonical_launcher"
        and "start_phoenixguard_full_local.ps1" in launcher_text
        and "FINAL_LIVE" in engine_launcher_text
        and "Legacy V1/V2: OFF" in engine_launcher_text
        and "Startup Test Signal: REMOVED" in engine_launcher_text
    )
    print(status_line("FINAL_LIVE canonical launch profile", profile_ok))
    if not profile_ok:
        failures.append("FINAL_LIVE canonical launch profile")

    runtime_dir = ROOT / ".codex_runtime"
    runtime_entries = [path for path in runtime_dir.iterdir() if runtime_entry_is_active(path)] if runtime_dir.exists() else []
    if runtime_entries:
        preview = ", ".join(path.name for path in runtime_entries[:6])
        suffix = "" if len(runtime_entries) <= 6 else f", +{len(runtime_entries) - 6} more"
        runtime_detail = f"active runtime state present ({preview}{suffix}); use clean_v3_runtime_state.py --apply before cold launch"
    else:
        runtime_detail = "CLEAN"
    print(status_line("Runtime Cache", True, runtime_detail))

    if args.base_url:
        endpoints = [
            "/v1/mobile/health",
            f"/v1/mobile/model-council/latest?session_id={args.session}",
            f"/v1/mobile/model-council/study/latest?session_id={args.session}",
            f"/v1/mobile/model-council/execution/latest?session_id={args.session}",
            f"/v1/mobile/floating/state?session_id={args.session}",
            f"/v1/mobile/shooter/handshake?session_id={args.session}",
            f"/v1/mobile/runtime/trace/v3?session_id={args.session}",
        ]
        for endpoint in endpoints:
            ok, detail = endpoint_ok(args.base_url, endpoint, args.timeout)
            print(status_line(f"Endpoint {endpoint}", ok, detail))
            if not ok:
                failures.append(endpoint)

    overall = not failures
    print(status_line("Overall", overall))
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
