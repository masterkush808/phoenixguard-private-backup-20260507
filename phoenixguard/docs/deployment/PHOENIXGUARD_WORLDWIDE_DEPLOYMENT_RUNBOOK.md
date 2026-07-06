# PhoenixGuard Worldwide Deployment Runbook

This runbook moves PhoenixGuard off the developer PC and onto always-on
infrastructure while preserving the core live-tracker truth:

```text
broker/browser chart surface -> PhoenixGuard tracker worker -> V3 API/dashboard -> playbook/MT4 bridge
```

PhoenixGuard currently depends on a real browser/window capture loop. That means
the live tracker cannot be replaced by static hosting or serverless functions
alone. A public dashboard can be hosted globally, but the chart-capture worker
must run on an always-on Windows desktop host.

## Deployment Shape

Use this production shape:

```text
Users worldwide
  -> HTTPS dashboard/API domain
  -> secure tunnel or load balancer
  -> Windows tracker worker(s)
       - Edge/Chrome broker session
       - PhoenixGuard live tracker
       - V3 Model Council and Playbook
       - package reporter
       - optional MT4 bridge/terminal
```

There are two valid access models.

## Model A: Managed Cloud Worker

PhoenixGuard runs the broker browser on a Windows VPS. Users access the
PhoenixGuard dashboard through a public HTTPS URL. Your PC can be off.

Best for:

```text
centralized tracking
managed broker sessions
MT4 bridge on the same worker
24/7 uptime supervision
```

Minimum worker target:

```text
Windows Server 2022
8 vCPU
16 GB RAM minimum, 32 GB preferred
150 GB+ NVMe
Chrome or Edge installed
Python 3.11 x64
stable region close to broker/MT4
```

Start CPU-only unless live latency proves that GPU inference is required. GPU
workers are materially more expensive and should be added only after measuring
real model latency.

## Model B: User Edge Agent

If users must track a browser on their own PC, the cloud cannot directly see
their local screen. They need a small local PhoenixGuard capture agent that sends
frames/state to the cloud API. Your PC can still be off, but each user providing
their own broker screen must keep their own machine/session online.

Best for:

```text
user-owned broker sessions
different brokers/regions/accounts
no broker credentials stored centrally
```

This requires a separate installable lightweight edge agent. The current repo is
ready for the managed-worker model first.

## MVP Production Deployment

1. Rent one Windows VPS.
2. RDP into it as the dedicated PhoenixGuard Windows user.
3. Install Git, Python 3.11 x64, Chrome or Edge, and MT4 if bridge execution is
   required.
4. Clone the repo.
5. Run:

```powershell
Set-ExecutionPolicy -Scope Process Bypass -Force
.\Developer\deployment\windows_worker_bootstrap.ps1 `
  -RepoUrl "https://github.com/masterkush808/phoenixguard-private-backup-20260507.git" `
  -InstallRoot "C:\PhoenixGuard" `
  -Branch "main" `
  -SessionId "pocket-live-8788" `
  -BrokerWindowQuery "The Most Innovative Trading Platform" `
  -CaptureIntervalSec 15 `
  -DashboardBrowser edge `
  -RegisterScheduledTasks
```

6. Open the broker on the VPS browser and sign in.
7. Lock the desired chart surface through the PhoenixGuard dashboard.
8. Expose the local dashboard using a secure tunnel or private gateway.

## Public Access

Do not expose `http://127.0.0.1:8793` directly to the internet.

Preferred MVP:

```text
Cloudflare Tunnel or Tailscale Funnel
public HTTPS domain -> localhost:8793 on the Windows worker
```

Later production:

```text
dedicated control-plane API
auth gateway
worker registry
database-backed session state
object storage for screenshots/evidence
WebSocket event stream
```

## Uptime Plan

True 100% uptime is not a realistic claim. The professional target is:

```text
single worker: best-effort 24/7, watchdog supervised
two workers: failover capable
control plane: 99.9%+ target
```

Add these layers:

```text
Windows auto-login for the PhoenixGuard worker user
scheduled task at user logon for the live stack
watchdog scheduled task every 5 minutes
cloud VM auto-restart
daily snapshot
Cloudflare/Tailscale tunnel auto-reconnect
external uptime monitor
disk-retention policy for screenshots/artifacts
MT4 bridge fail-closed on stale packages
```

## What You Need To Pay For

You need at least one paid always-on Windows machine.

Approximate starting points as of July 2026:

```text
Budget Windows VPS: roughly $25-$60/month
Serious CPU worker: roughly $80-$200/month
GPU worker: often $0.50-$3+/hour depending GPU/provider
Domain: roughly $10-$20/year
Cloudflare Tunnel: can start free
Backups/snapshots: extra monthly cost
```

Recommended first purchase:

```text
Windows VPS/VDS
8 vCPU
16-32 GB RAM
150 GB+ NVMe
monthly budget: $80-$150
```

If CPU inference is too slow, move model inference to a GPU worker and keep the
browser/MT4 worker on Windows.

## Security Rules

```text
Do not commit broker credentials.
Do not expose the raw mobile API port publicly.
Use HTTPS.
Use authentication before allowing dashboard access.
Use fresh package TTLs for MT4.
Fail closed if source lock, frame freshness, or bridge heartbeat fails.
Keep production runtime separate from dev/training environments.
```

## Deployment Readiness Checklist

```text
repo is pushed to main
.venv-live installs from requirements/locks/live-win-py311.txt
PhoenixGuard health endpoint returns ok
single API listener on 8793
tracker captures fresh frames every configured interval
compact live state responds under normal load
package reporter is running
MT4 bridge is running only if intended
Cloudflare/Tailscale tunnel is authenticated
public URL requires authentication
watchdog task is enabled
VM snapshot is configured
```
