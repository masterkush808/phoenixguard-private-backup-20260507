# PhoenixGuard Developer Read First

This is the safe local runbook for the final V3 architecture. Run all commands from PowerShell at
the repo root:

```powershell
Set-Location "C:\Users\thaba\OneDrive\Documents\The 808 Vision 2026\phoenixguard"
Set-ExecutionPolicy -Scope Process Bypass -Force
.\.venv\Scripts\Activate.ps1
```

## Fast Safe Restart

Use this when you want to stop stale sessions/processes, clear runtime cache, and start the live
dashboard without any floating editor window.

```powershell
$base = "http://127.0.0.1:8793"
$session = "pocket-live-8788"
$root = (Get-Location).Path

# Ask the running tracker to stop first if the API is alive.
try { Invoke-RestMethod -Method Post "$base/v1/mobile/window-tracker/sessions/$session/emergency-stop" -TimeoutSec 5 } catch {}
try { Invoke-RestMethod -Method Post "$base/v1/mobile/window-tracker/sessions/$session/stop" -TimeoutSec 5 } catch {}

# Kill PhoenixGuard processes launched from this repo or known runtime entrypoints.
Get-CimInstance Win32_Process |
    Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        (
            $_.CommandLine -like "*$root*" -or
            $_.CommandLine -match "shooter\.py|start_phoenixguard|launch_phoenixguard|window_tracker|uvicorn.*phoenixguard"
        )
    } |
    ForEach-Object {
        Write-Host "Stopping PID $($_.ProcessId): $($_.Name)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Start-Sleep -Seconds 3

# Back up and clear stale runtime/cache state. This preserves models, memory,
# user configuration, reports, and current package-reporter state boundaries.
python .\tools\clean_v3_runtime_state.py --apply
if ($LASTEXITCODE -ne 0) { throw "Runtime cleanup failed. Launch aborted." }

# Canonical final V3 live launch. -NoBrowser prevents popup/editor launch.
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch_phoenixguard_live_ready.ps1 -NoBrowser
```

Open the dashboard after launch:

```text
http://127.0.0.1:8793/dashboard/live/pocket-live-8788
```

## Read The PhoenixGuard State

Use these after launch to confirm freshness, trace integrity, and what the current PhoenixGuard read
says.

```powershell
$base = "http://127.0.0.1:8793"
$session = "pocket-live-8788"

# Public live read used by the dashboard.
Invoke-RestMethod "$base/v1/mobile/live/state/v3/$session?mode=CLEAN_LIVE" |
    ConvertTo-Json -Depth 12

# Backend/frontend timing and stale-frame diagnosis.
Invoke-RestMethod "$base/v1/mobile/performance/trace/v3/$session" |
    ConvertTo-Json -Depth 12

# Full V3 runtime trace: source lock, frame buffer, sequence, council, packet, package reporter.
Invoke-RestMethod "$base/v1/mobile/runtime/trace/v3?session_id=$session" |
    ConvertTo-Json -Depth 16

# CLI summaries for quick pass/fail reads.
python .\tools\runtime_trace_v3.py --base-url $base --session $session --timeout 20
python .\tools\trace_sequence_context_v3.py --base-url $base --session $session --timeout 20
python .\tools\verify_v3_integrity.py
```

`verify_v3_integrity.py` should report `Overall: PASS` before you treat the runtime as
production-ready.

## Start Or Stop Only The Tracker Session

When the API is already running and you only need to restart the tracker:

```powershell
$base = "http://127.0.0.1:8793"
$session = "pocket-live-8788"

Invoke-RestMethod -Method Post "$base/v1/mobile/window-tracker/sessions/$session/stop"
Invoke-RestMethod -Method Post "$base/v1/mobile/window-tracker/sessions/$session/start"
```

Create the default session manually if it does not exist:

```powershell
$body = @{
    session_id = "pocket-live-8788"
    name = "Pocket Option Live"
    window_query = "Pocket Option"
    layout_profile = "auto"
    capture_interval_sec = 1.0
    auto_start = $true
} | ConvertTo-Json

Invoke-RestMethod -Method Post "$base/v1/mobile/window-tracker/sessions" `
    -ContentType "application/json" `
    -Body $body
```

## API-Only Developer Fallback

Use this only for debugging the backend API. It does not arm the full production launcher flow or
shooter process.

```powershell
$env:PYTHONPATH = (Get-Location).Path
python -m uvicorn phoenixguard.mobile_api.app:create_app --factory --host 127.0.0.1 --port 8793 --log-level info
```

## File To Launch Safely

For normal developer work, use:

```text
launch_phoenixguard_live_ready.ps1
```

That file is the canonical final V3 live launcher. It starts the mobile API, creates/starts the
`pocket-live-8788` tracker session, keeps the dashboard on the final V3 path, and only enables
shooter behavior through the packet-gated live stack. Use `-DisableShooter` for read-only
development:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch_phoenixguard_live_ready.ps1 -NoBrowser -DisableShooter
```

# PhoenixGuard V3

PhoenixGuard V3 is a `FINAL_LIVE` chart-intelligence workstation. The live path is strictly:

```text
BrokerSourceLockV3
-> LatestFrameBufferV3
-> ChartSegmentationV3
-> CandleObjectTrackerV3
-> MarketObjectTrackerV3
-> SequenceContextV3
-> ModelCouncilV3
-> STUDY_PACKET or PG_EXECUTION_PACKET_V3
-> RuntimeTraceV3
-> Dashboard/FloatingStateV2
-> Shooter Package Reporter
-> MT4 Bridge / External Execution Path
```

Observation is not execution. Study packets are not execution. Skill gates are diagnostic
contributors. The local `shooter.py` process no longer clicks or calibrates broker controls; it
reports only fresh accepted allowance packages derived from a validated `PG_EXECUTION_PACKET_V3`.

## Clean Kill, Reset, And Launch

Run this from PowerShell when you need to kill all PhoenixGuard processes, clear runtime/cache
state, and launch the production stack.

```powershell
Set-Location "C:\Users\thaba\OneDrive\Documents\The 808 Vision 2026\phoenixguard"

# Kill PhoenixGuard-related processes that were launched from this repo or match
# known PhoenixGuard runtime entrypoints.
$root = (Get-Location).Path
Get-CimInstance Win32_Process |
    Where-Object {
        $_.ProcessId -ne $PID -and
        $_.CommandLine -and
        (
            $_.CommandLine -like "*$root*" -or
            $_.CommandLine -match "shooter\.py|start_phoenixguard|launch_phoenixguard|window_tracker|uvicorn.*phoenixguard"
        )
    } |
    ForEach-Object {
        Write-Host "Stopping PID $($_.ProcessId): $($_.Name)"
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }

Start-Sleep -Seconds 3

# Activate the local environment.
.\.venv\Scripts\Activate.ps1

# Clear V3 runtime/cache state before a cold launch.
python .\tools\clean_v3_runtime_state.py --apply
if ($LASTEXITCODE -ne 0) {
    throw "Runtime cleanup failed. Launch aborted."
}

# Launch the full live-ready V3 stack.
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch_phoenixguard_live_ready.ps1 -NoBrowser
```

If more than one matching Edge/Chrome broker window is open, pin the shooter to the broker surface
locked by the tracker:

```powershell
$state = Invoke-RestMethod "http://127.0.0.1:8793/v1/mobile/live/state/v3/pocket-live-8788?mode=CLEAN_LIVE"
$hwnd = [int]$state.broker_source_lock.selected_target.window_handle
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch_phoenixguard_live_ready.ps1 -NoBrowser -BrokerWindowHwnd $hwnd
```

The launcher also attempts this HWND detection automatically before arming the shooter. Passing
`-BrokerWindowHwnd` is the explicit override for a busy desktop.

The production launcher sets the live tracker hot path to low latency:

```powershell
$env:PHOENIXGUARD_ARTIFACT_PNG_COMPRESS_LEVEL="0"
$env:PHOENIXGUARD_LIVE_MINIMAL_HOT_ARTIFACTS="1"
$env:PHOENIXGUARD_LIVE_FULL_OVERLAY_EVERY_N="300"
$env:PHOENIXGUARD_LIVE_CANDLE_MAX_WIDTH="320"
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT="1"
$env:PHOENIXGUARD_LIVE_FAST_DISPLAY_HEARTBEAT_SEC="0.5"
$env:PHOENIXGUARD_FAST_FOCUS_PREVIEW="1"
$env:PHOENIXGUARD_ENABLE_LIVE_SCENARIO_GENERATION="0"
```

These keep fresh frame, model, packet, and sequence state moving while avoiding slow full-overlay
artifact writes on every live frame and bounding candle-track extraction cost on large broker
windows. The fast-display heartbeat refreshes the latest broker screenshot while the study worker is
busy, and fast focus preview avoids a blocking full study before tracker start; the heartbeat uses
display-only capture and neither path creates execution authority. Scenario generation remains
diagnostic/offline by default in live execution mode so it cannot slow the hot path; set
`PHOENIXGUARD_ENABLE_LIVE_SCENARIO_GENERATION=1` only for an intentional diagnostics run.

Read-only launch, with tracker/dashboard active and shooter disabled:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch_phoenixguard_live_ready.ps1 -NoBrowser -DisableShooter
```

## Post-Launch Verification

After launch, verify runtime trace, sequence context, and canonical integrity:

```powershell
python .\tools\runtime_trace_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --timeout 20
python .\tools\trace_sequence_context_v3.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --timeout 20
python .\tools\verify_v3_integrity.py
```

`verify_v3_integrity.py` must report `Overall: PASS` before treating the runtime as
production-ready.

Two-hour activated burn-in:

```powershell
python .\tools\certify_v3_full_system_burn_in.py --base-url http://127.0.0.1:8793 --session pocket-live-8788 --duration-sec 7200 --interval-sec 10 --warmup-sec 60 --status-every-sec 60 --timeout 20 --mode FULL_ACTIVATED --max-frame-age-ms 2500 --max-consecutive-stale-frames 12 --max-consecutive-process-misses 5 --no-stop-on-stale-frame --no-stop-on-stale-execution-packet --out reports\FINAL_FULL_SYSTEM_ACTIVATED_BURN_IN_OVERLAYFIXED_FINAL_REPORT.md
```

For long live observation runs where transient Windows capture latency should be logged but not
abort the run, add `--no-stop-on-stale-frame --no-stop-on-stale-execution-packet`. The shooter still
refuses expired packets through runtime integrity validation.

## Shooter Package Reporter Safety

The old calibrated broker-click shooter has been retired. `shooter.py` is now a package reporter:

- `main.py::run_inference` is offline/manual analysis only.
- Observer signals, dashboard state, overlays, and skill gates are diagnostic.
- `shooter.py` reads the Model Council execution endpoint only.
- It writes `.codex_runtime\shooter_handshake.json` only when the packet carries an accepted,
  execution-ready `PG_ALLOWANCE_PACKAGE_V1`.
- It never reads calibration files, moves the mouse, sets broker time, edits amount, or clicks
  BUY/SELL.
- Downstream external execution, when enabled, must consume the validated package handoff through
  the MT4 bridge or another explicit external path.

To run the reporter once after verification:

```powershell
python shooter.py --base-url http://127.0.0.1:8793 --session-id pocket-live-8788 --once
```

To keep the reporter polling:

```powershell
python shooter.py --base-url http://127.0.0.1:8793 --session-id pocket-live-8788 --poll 0.20
```

Package authority:

- `PG_EXECUTION_PACKET_V3` remains the only execution-authority packet.
- `PG_ALLOWANCE_PACKAGE_V1` classifies the allowed package as `INTRADAY_ENTER_NOW` or `SWING`.
- The reporter rejects missing, inferred, stale, non-accepted, or non-execution-ready packages.
- MT4 bridge commands must carry the explicit Model Council allowance package.

## Core Runtime Contracts

- `raw_side`: observation only.
- `candidate_side`: side under evaluation.
- `final_side`: Model Council arbitration result.
- `execution.side`: the only actionable side.
- `STUDY_PACKET`: explanatory, never executable.
- `PG_EXECUTION_PACKET_V3`: only valid execution authority.

Every execution packet must carry provenance:

```text
frame_id, capture_count, state_version, sequence_id, source_lock_id,
model_health_id, chart_transform_id, created_epoch_ms, valid_until_epoch_ms
```

Every live state must be explainable through RuntimeTraceV3 dataflow and certification gates: source
lock, frame freshness, sequence context, model warm state, overlay truth, Model Council trace,
packet contract, shooter persistence, and burn-in evidence.

## Validation Commands

Focused Grade A\* hardening checks:

```powershell
python -m pytest tests/test_execution_packet_schema_v3.py tests/test_model_council_v3.py tests/test_market_reality_engine.py tests/test_market_intelligence_v3.py tests/test_v3_language_contracts.py tests/test_simulation_paper_execution.py tests/test_mt4_file_bridge.py -q
python -m pytest tests/test_entry_allowance_burn.py tests/test_business_commands.py -q
python -m pytest tests/test_cache_observability_v3.py tests/test_runtime_telemetry_v3.py tests/test_manual_inference_queue.py -q
python -m compileall -q main.py shooter.py phoenixguard\decision phoenixguard\execution phoenixguard\mobile_api phoenixguard\runtime tools\runtime_trace_v3.py tools\certification_common_v3.py
python tools\verify_v3_integrity.py
```

## TradingView Study Source

TradingView may be used as a chart-study source while any real execution path remains external to
the local package reporter. Keep study sessions separated from broker/external bridge sessions:

```powershell
python start_phoenixguard_24_7_tracker.py --session-id tradingview-study --window-query "TradingView" --focus-region ""
```

## Production Artifacts

- `launch_phoenixguard_live_ready.ps1`: canonical production launcher.
- `shooter.py`: local package reporter, not a broker-click executor.
- `.codex_runtime\`: live runtime state, traces, packet cache, handshakes, and evidence.
- `reports\`: launch, trace, validation, and certification outputs.

## Tracing

PhoenixGuard initializes OpenTelemetry tracing automatically when the mobile API or workstation
launches.

- Default export endpoint: `http://localhost:4318/v1/traces`
- Disable with `PHOENIXGUARD_TRACING_DISABLED=1`
- Override with `PHOENIXGUARD_OTLP_ENDPOINT` or `OTEL_EXPORTER_OTLP_ENDPOINT`
- Override service labels with `PHOENIXGUARD_TRACE_SERVICE_NAME` or `OTEL_SERVICE_NAME`

## License

Proprietary. All rights reserved.
