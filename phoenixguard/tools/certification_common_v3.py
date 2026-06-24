from __future__ import annotations

from dataclasses import dataclass
from http.client import HTTPException
import json
import math
import os
from pathlib import Path
import socket
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping, Sequence, cast


ROOT = Path(__file__).resolve().parents[1]
CERT_DIR = ROOT / "reports" / "certification"
DEFAULT_BASE_URL = "http://127.0.0.1:8793"
DEFAULT_SESSION = "pocket-live-8788"
_LOCAL_APP_DATA = str(os.getenv("LOCALAPPDATA", "") or "").strip()
DEFAULT_DATA_DIR = Path(
    os.getenv("PHOENIXGUARD_DATA_DIR")
    or (
        Path(_LOCAL_APP_DATA) / "PhoenixGuard" / "codex_runtime" / "data_live"
        if _LOCAL_APP_DATA
        else ROOT / ".codex_runtime" / "data_live"
    )
)


@dataclass(frozen=True)
class HttpResult:
    ok: bool
    status: int
    latency_ms: float
    payload: Any = None
    bytes_len: int = 0
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": bool(self.ok),
            "status": int(self.status),
            "latency_ms": round(float(self.latency_ms), 3),
            "payload": self.payload,
            "bytes": int(self.bytes_len),
            "error": self.error,
        }


def now_epoch() -> float:
    return time.time()


def quote_session(session_id: str) -> str:
    return urllib.parse.quote(session_id, safe="")


def normalize_path_text(value: str | Path) -> str:
    try:
        return str(Path(value).resolve()).replace("\\", "/").lower()
    except Exception:
        return str(value).replace("\\", "/").lower()


def error_text(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}: {exc.reason}"
    if isinstance(exc, urllib.error.URLError):
        return f"URL error: {exc.reason}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return f"timeout: {exc}"
    if isinstance(exc, HTTPException):
        return f"HTTP error: {exc}"
    return f"{type(exc).__name__}: {exc}"


def http_json(
    url: str,
    *,
    method: str = "GET",
    timeout: float = 5.0,
    payload: Mapping[str, Any] | None = None,
) -> HttpResult:
    data = None
    headers = {
        "User-Agent": "PhoenixGuard-V3-Certification/1.0",
        "Connection": "close",
    }
    if payload is not None:
        data = json.dumps(dict(payload)).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif method.upper() in {"POST", "PUT", "PATCH"}:
        data = b""
    started = time.perf_counter()
    request = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local operator endpoint
            body = response.read()
            latency_ms = (time.perf_counter() - started) * 1000.0
            parsed: Any = {}
            if body:
                parsed = json.loads(body.decode("utf-8", errors="replace"))
            return HttpResult(
                ok=200 <= int(response.status) < 300,
                status=int(response.status),
                latency_ms=latency_ms,
                payload=parsed,
                bytes_len=len(body),
            )
    except Exception as exc:
        return HttpResult(ok=False, status=getattr(exc, "code", 0) or 0, latency_ms=(time.perf_counter() - started) * 1000.0, error=error_text(exc))


def http_bytes(url: str, *, timeout: float = 5.0) -> HttpResult:
    started = time.perf_counter()
    request = urllib.request.Request(url, headers={"User-Agent": "PhoenixGuard-V3-Certification/1.0", "Connection": "close"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - local operator endpoint
            body = response.read()
            return HttpResult(
                ok=200 <= int(response.status) < 300,
                status=int(response.status),
                latency_ms=(time.perf_counter() - started) * 1000.0,
                payload=None,
                bytes_len=len(body),
            )
    except Exception as exc:
        return HttpResult(ok=False, status=getattr(exc, "code", 0) or 0, latency_ms=(time.perf_counter() - started) * 1000.0, error=error_text(exc))


def percentile(values: Sequence[float], pct: float) -> float:
    clean = sorted(float(value) for value in values if math.isfinite(float(value)))
    if not clean:
        return 0.0
    index = min(len(clean) - 1, max(0, int(round((len(clean) - 1) * pct / 100.0))))
    return round(float(clean[index]), 3)


def summarize_numbers(values: Sequence[float]) -> dict[str, float]:
    clean = [float(value) for value in values if math.isfinite(float(value))]
    return {
        "count": float(len(clean)),
        "avg": round(sum(clean) / len(clean), 3) if clean else 0.0,
        "p95": percentile(clean, 95),
        "p99": percentile(clean, 99),
        "max": round(max(clean), 3) if clean else 0.0,
    }


def run_powershell_json(script: str, *, timeout: float = 20.0) -> list[Any]:
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    text = (completed.stdout or "").strip()
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "").strip())
    if not text:
        return []
    parsed: object = json.loads(text)
    return list(cast(list[Any], parsed)) if isinstance(parsed, list) else [parsed]


def python_processes() -> list[dict[str, Any]]:
    script = """
Get-CimInstance Win32_Process -Filter "name = 'python.exe'" |
  Select-Object ProcessId, ParentProcessId, CommandLine |
  ConvertTo-Json -Depth 4
"""
    try:
        rows = run_powershell_json(script)
    except Exception as exc:
        return _python_processes_wmic(str(exc))
    return [dict(cast(Mapping[str, Any], row)) for row in rows if isinstance(row, Mapping)]


def _python_processes_wmic(primary_error: str) -> list[dict[str, Any]]:
    try:
        completed = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                "name='python.exe'",
                "get",
                "CommandLine,ParentProcessId,ProcessId",
                "/VALUE",
            ],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except Exception as exc:
        return [{"error": f"{primary_error}; wmic fallback failed: {error_text(exc)}"}]
    if completed.returncode != 0:
        return [{"error": f"{primary_error}; wmic fallback failed: {(completed.stderr or completed.stdout or '').strip()}"}]
    text = (completed.stdout or "").strip()
    if not text:
        return [{"error": f"{primary_error}; wmic fallback returned no output"}]
    rows: list[dict[str, Any]] = []
    current: dict[str, str] = {}
    try:
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                if current.get("ProcessId"):
                    rows.append(
                        {
                            "ProcessId": int(current.get("ProcessId") or 0),
                            "ParentProcessId": int(current.get("ParentProcessId") or 0),
                            "CommandLine": current.get("CommandLine", ""),
                        }
                    )
                current = {}
                continue
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()
        if current.get("ProcessId"):
            rows.append(
                {
                    "ProcessId": int(current.get("ProcessId") or 0),
                    "ParentProcessId": int(current.get("ParentProcessId") or 0),
                    "CommandLine": current.get("CommandLine", ""),
                }
            )
    except Exception as exc:
        return [{"error": f"{primary_error}; wmic fallback parse failed: {error_text(exc)}"}]
    return rows or [{"error": f"{primary_error}; wmic fallback found no python processes"}]


def tcp_listeners(ports: Sequence[int]) -> list[dict[str, Any]]:
    port_text = ",".join(str(int(port)) for port in ports)
    script = f"""
Get-NetTCPConnection -State Listen |
  Where-Object {{ $_.LocalPort -in @({port_text}) }} |
  Select-Object LocalAddress, LocalPort, OwningProcess |
  ConvertTo-Json -Depth 4
"""
    try:
        rows = run_powershell_json(script)
    except Exception as exc:
        return _tcp_listeners_netstat(ports, str(exc))
    return [dict(cast(Mapping[str, Any], row)) for row in rows if isinstance(row, Mapping)]


def _tcp_listeners_netstat(ports: Sequence[int], primary_error: str) -> list[dict[str, Any]]:
    wanted = {int(port) for port in ports}
    try:
        completed = subprocess.run(
            ["netstat", "-ano", "-p", "tcp"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=10.0,
            check=False,
        )
    except Exception as exc:
        return [{"error": f"{primary_error}; netstat fallback failed: {error_text(exc)}"}]
    if completed.returncode != 0:
        return [{"error": f"{primary_error}; netstat fallback failed: {(completed.stderr or completed.stdout or '').strip()}"}]
    rows: list[dict[str, Any]] = []
    for line in (completed.stdout or "").splitlines():
        parts = line.split()
        if len(parts) < 5 or parts[0].upper() != "TCP" or parts[-2].upper() != "LISTENING":
            continue
        local = parts[1]
        try:
            port = int(local.rsplit(":", 1)[1])
        except Exception:
            continue
        if port not in wanted:
            continue
        rows.append(
            {
                "LocalAddress": local.rsplit(":", 1)[0],
                "LocalPort": port,
                "OwningProcess": int(parts[-1]),
            }
        )
    return rows or [{"error": f"{primary_error}; netstat fallback found no matching listeners"}]


def command_line(row: Mapping[str, Any]) -> str:
    return str(row.get("CommandLine") or row.get("command_line") or "")


def process_id(row: Mapping[str, Any]) -> int:
    try:
        return int(row.get("ProcessId") or row.get("pid") or 0)
    except Exception:
        return 0


def parent_process_id(row: Mapping[str, Any]) -> int:
    try:
        return int(row.get("ParentProcessId") or row.get("parent_pid") or 0)
    except Exception:
        return 0


def find_processes(rows: Sequence[Mapping[str, Any]], needle: str) -> list[dict[str, Any]]:
    target = needle.lower()
    return [dict(row) for row in rows if target in command_line(row).lower()]


def leaf_processes(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    process_ids = {process_id(row) for row in rows if process_id(row)}
    return [dict(row) for row in rows if parent_process_id(row) not in process_ids]


def report_path(name: str) -> Path:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    return CERT_DIR / name


def write_report(name: str, report: Mapping[str, Any]) -> Path:
    path = report_path(name)
    path.write_text(json.dumps(dict(report), indent=2, sort_keys=True, default=str), encoding="utf-8")
    return path


def print_gate(label: str, report: Mapping[str, Any]) -> None:
    summary: dict[str, Any] = {
        "gate": label,
        "verdict": report.get("verdict", "FAIL"),
        "failures": report.get("failures", []),
        "out": report.get("out_json"),
    }
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))


def gate_report(
    *,
    schema_version: str,
    gate: str,
    failures: Sequence[str],
    warnings: Sequence[str] | None = None,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "schema_version": schema_version,
        "gate": gate,
        "generated_epoch": time.time(),
        "verdict": "PASS" if not failures else "FAIL",
        "failures": list(failures),
        "warnings": list(warnings or []),
    }
    if details:
        report.update(dict(details))
    return report


def _mapping(value: object) -> dict[str, Any]:
    return dict(cast(Mapping[str, Any], value)) if isinstance(value, Mapping) else {}


def _float_value(value: object, default: float = 0.0) -> float:
    if not isinstance(value, (int, float, str)):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _int_value(value: object, default: int = 0) -> int:
    return int(_float_value(value, float(default)))


def extract_frame_fields(payload: Mapping[str, Any]) -> dict[str, Any]:
    timing = _mapping(payload.get("frame_timing_trace_v3"))
    surface = _mapping(payload.get("broker_surface_frame"))
    return {
        "frame_id": _int_value(payload.get("frame_id") or payload.get("frame_index") or payload.get("capture_count") or 0),
        "display_frame_id": _int_value(payload.get("display_frame_id") or surface.get("frame_id") or payload.get("frame_index") or 0),
        "display_capture_epoch": _float_value(payload.get("display_capture_epoch") or surface.get("capture_epoch") or 0.0),
        "display_published_epoch": _float_value(payload.get("display_published_epoch") or surface.get("published_epoch") or 0.0),
        "chart_frame_id": _int_value(payload.get("chart_frame_id") or payload.get("frame_index") or 0),
        "overlay_frame_id": _int_value(payload.get("overlay_frame_id") or payload.get("frame_index") or 0),
        "model_vote_frame_id": _int_value(payload.get("model_vote_frame_id") or payload.get("frame_index") or 0),
        "model_capture_epoch": _float_value(payload.get("model_capture_epoch") or (_float_value(timing.get("model_capture_epoch_ms"), 0.0) / 1000.0) or 0.0),
        "state_version": _int_value(payload.get("state_version") or 0),
        "source_capture_id": str(payload.get("source_capture_id") or ""),
    }


def write_final_certification_report(results: Sequence[Mapping[str, Any]], *, out_path: Path | None = None) -> Path:
    target = out_path or ROOT / "reports" / "FINAL_V3_CERTIFICATION_REPORT.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    gate_names = [
        "Process Topology",
        "Capture Worker",
        "Atomic Session State",
        "API Stability",
        "Freshness and Speed",
        "Dashboard Hydration",
        "Model Warm-State",
        "Broker Source Lock",
        "Wrong Surface Rejection",
        "Shooter Persistence",
        "Overlay Visual Truth",
        "Overlay Mode Wiring",
        "Burn-In",
    ]
    by_gate = {str(row.get("gate", "")): row for row in results}
    lines = ["# PhoenixGuard V3 Certification Report", ""]
    overall_pass = True
    for index, name in enumerate(gate_names, start=1):
        row = by_gate.get(name, {})
        verdict = str(row.get("verdict", "NOT_RUN") or "NOT_RUN")
        if verdict != "PASS":
            overall_pass = False
        lines.append(f"- Gate {index} {name}: {verdict}")
    lines.extend(["", f"Overall certification: {'PASS' if overall_pass else 'FAIL'}", ""])
    for row in results:
        failures = list(row.get("failures") or []) if isinstance(row.get("failures"), Sequence) and not isinstance(row.get("failures"), (str, bytes)) else []
        if failures:
            lines.append(f"## {row.get('gate', 'Gate')}")
            lines.extend(f"- {item}" for item in failures[:10])
            lines.append("")
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return target
