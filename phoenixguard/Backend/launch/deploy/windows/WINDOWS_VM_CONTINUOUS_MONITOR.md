# PhoenixGuard Continuous Tracker/Shooter VM

This runbook is for the live monitoring stack:

- tracker API on `8793`
- locked-window tracker session `pocket-live-8788`
- shooter polling the tracker every `1.0` second
- monitor watchdog restarting tracker and shooter if either exits

## Required VM Shape

Use a Windows VM with an interactive desktop session. The tracker captures a visible broker window and the shooter may click the desktop, so the monitor should run as the logged-in VM operator, not as `SYSTEM`.

Recommended project path:

```powershell
C:\PhoenixGuard\phoenixguard
```

Do not run the VM copy from OneDrive or another synced folder.

## Configure

```powershell
cd C:\PhoenixGuard\phoenixguard
Copy-Item .\Backend\launch\deploy\windows\phoenixguard.vm-monitor.env.example.ps1 .\Backend\launch\deploy\windows\phoenixguard.vm-monitor.env.ps1
notepad .\Backend\launch\deploy\windows\phoenixguard.vm-monitor.env.ps1
```

Keep these defaults for tracker + shooter on the same VM:

```powershell
$env:PHOENIXGUARD_MONITOR_BIND_HOST = '127.0.0.1'
$env:PHOENIXGUARD_MONITOR_BASE_HOST = '127.0.0.1'
$env:PHOENIXGUARD_MOBILE_API_PORT = '8793'
$env:PHOENIXGUARD_TRACKER_CAPTURE_INTERVAL_SEC = '1.0'
$env:PHOENIXGUARD_SHOOTER_POLL_SEC = '1.0'
```

Use `0.0.0.0` only if the VM firewall, VPN, or authenticated tunnel protects port `8793`.

## First Start

```powershell
cd C:\PhoenixGuard\phoenixguard
.\Backend\launch\deploy\windows\Start-PhoenixGuardVmMonitor.ps1 -Bootstrap
```

Open the dashboard inside the VM:

```powershell
Start-Process http://127.0.0.1:8793/v1/mobile/window-tracker/dashboard/pocket-live-8788
```

Lock the Pocket Option chart in the dashboard. The shooter can run while waiting, but it only consumes valid fresh tracker decisions.

## Auto Start On VM Logon

Run this inside the logged-in VM operator account:

```powershell
cd C:\PhoenixGuard\phoenixguard
.\Backend\launch\deploy\windows\Register-PhoenixGuardVmMonitorTask.ps1 -BootstrapOnFirstRun -StartNow
```

The task name is:

```text
PhoenixGuard VM Monitor
```

## Status Files

Monitor status:

```powershell
Get-Content .\.codex_runtime\vm_monitor_status.json -Raw
```

Tracker status:

```powershell
Get-Content .\.codex_runtime\tracker_status.json -Raw
```

Logs:

```powershell
Get-ChildItem .\.codex_runtime\vm_monitor_logs
```

## VM Hosted Tracker With Local Shooter

On the VM config, expose the tracker behind a private boundary:

```powershell
$env:PHOENIXGUARD_MONITOR_BIND_HOST = '0.0.0.0'
$env:PHOENIXGUARD_MONITOR_BASE_HOST = '127.0.0.1'
```

Then from the local shooter machine:

```powershell
python Backend\launch\shooter.py signal --session-id pocket-live-8788 --base-url http://VM_PRIVATE_IP:8793 --poll 1.0 --preferred-source tracker --require-preferred-source --min-confidence 0.58 --max-signal-age 8
```

Do not expose `8793` unauthenticated to the public internet.
