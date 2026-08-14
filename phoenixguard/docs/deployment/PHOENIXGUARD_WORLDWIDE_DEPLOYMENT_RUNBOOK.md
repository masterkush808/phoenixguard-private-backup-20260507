# PhoenixGuard Worldwide Deployment Runbook

This runbook moves PhoenixGuard off the developer PC and onto always-on
infrastructure while preserving the core live-tracker truth:

```text
broker/browser chart surface -> PhoenixGuard tracker worker -> V3 API/dashboard -> playbook/MT4 bridge
```

PhoenixGuard can now study either a managed browser/window capture loop or
external chart frames pushed through the secured frame-ingest contract. Static
hosting or serverless functions still cannot run the full Python/CV/model brain,
but the chart source is no longer limited to the developer PC.

Security gate:

```text
docs/deployment/PHOENIXGUARD_SECURITY_HARDENING_GATE.md
```

## Deployment Shape

Use this production shape:

```text
Users worldwide
  -> HTTPS dashboard/API domain
  -> secure tunnel or load balancer
  -> PhoenixGuard brain worker(s)
       - V3 tracker, overlays, Model Council, Playbook
       - package reporter and optional MT4 bridge
       - managed Edge/Chrome broker session, or
       - external frame feeds from user/edge agents
```

The source contract is:

```text
chart pixels from any trusted feed
  -> POST /v1/mobile/frame-ingest/sessions/{session_id}/frames
  -> PhoenixGuard tracker study
  -> same V3 overlay/playbook/package path
```

There are three valid access models.

## Model A: Managed Cloud Worker

PhoenixGuard runs the broker browser on a Windows VPS. Users access the
PhoenixGuard dashboard through a public HTTPS URL. Your PC can be off.

For the managed PC/mobile source-feed workflow, see:

```text
docs/deployment/PHOENIXGUARD_UNIVERSAL_FRAME_FEED_WORKFLOW.md
```

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

## Model B: Universal Edge Frame Feed

PhoenixGuard runs off-machine as the cloud brain. A user machine, browser host,
MT4 chart box, cloud browser worker, or any trusted feed sends chart images into
PhoenixGuard every configured interval. Your PC can be off. A user who supplies
their own private broker screen must keep that feed online.

Best for:

```text
user-owned broker sessions
TradingView, Pocket Option, MT4, or future chart surfaces
different brokers/regions/accounts
no broker credentials stored centrally
cloud brain with replaceable frame input
```

The API is intentionally token-gated:

```text
POST /v1/mobile/frame-ingest/sessions/{session_id}/frames
Authorization: Bearer <PHOENIXGUARD_FRAME_INGEST_TOKEN>
X-PhoenixGuard-Signature-Alg: HMAC-SHA256-V1
X-PhoenixGuard-Timestamp: <epoch-ms>
X-PhoenixGuard-Nonce: <unique nonce>
X-PhoenixGuard-Signature: v1=<hmac-sha256>
multipart field: frame=<png/jpg/webp>
form fields: source_id, symbol, timeframe, source_url, sequence_id, capture_epoch_ms, frame_id, metadata_json
```

Required production env:

```powershell
$env:PHOENIXGUARD_FRAME_INGEST_TOKEN = "<long-random-secret>"
$env:PHOENIXGUARD_FRAME_INGEST_SIGNING_SECRET = "<separate-long-random-signing-secret>"
$env:PHOENIXGUARD_FRAME_INGEST_REQUIRE_SIGNATURE = "1"
$env:PHOENIXGUARD_FRAME_INGEST_MAX_SOURCE_AGE_SEC = "180"
```

Run the edge feed agent from a user/worker machine:

```powershell
python .\Developer\deployment\edge_frame_agent.py `
  --base-url "https://phoenixguard.example.com" `
  --session-id "edge-eurcad-m5" `
  --token "<long-random-secret>" `
  --signing-secret "<separate-long-random-signing-secret>" `
  --source-id "user-001-edge" `
  --source-url "https://pocketoption.com/en/cabinet/demo-quick-high-low/" `
  --symbol "EURCAD" `
  --timeframe "M5" `
  --bbox "80,140,1520,920" `
  --interval-sec 15
```

This mode is study/feed only. It does not make the remote frame source a local
click target. MT4 execution still consumes fresh validated V3 packages through
the bridge path.

## Model C: Public Advisory Dashboard

Users consume PhoenixGuard decisions, charts, and package evidence through a
web dashboard while one or more managed feeds supply frames.

Best for:

```text
worldwide access
public/private subscriptions
manual-trade advisory mode
centralized uptime and evidence retention
```

Long-term production shape:

```text
Cloudflare Access / auth gateway
  -> PhoenixGuard API
  -> session registry
  -> object storage for screenshots/evidence
  -> worker queue for frame jobs
  -> optional MT4 bridge nodes
```

## Legacy Managed-Window Shape

The original shape remains valid when PhoenixGuard owns the broker window:

```text
Windows tracker worker
       - Edge/Chrome broker session
       - PhoenixGuard live tracker
       - V3 Model Council and Playbook
       - package reporter
       - optional MT4 bridge/terminal
```

## MVP Production Deployment

### Cheapest Current Path

Use the Linux cloud-brain deployment first:

```text
Contabo Cloud VPS 20 or equivalent
Ubuntu 24.04 LTS
4 vCPU minimum
12 GB RAM preferred minimum
200 GB SSD/NVMe preferred
Cloudflare Tunnel for public HTTPS
edge_frame_agent.py for chart frames
```

This avoids Windows licensing cost and avoids paying for GPU before measured
latency proves that GPU is needed.

Server bootstrap:

```bash
sudo apt-get update && sudo apt-get install -y git
git clone https://github.com/masterkush808/phoenixguard-private-backup-20260507.git /tmp/phoenixguard-deploy
cd /tmp/phoenixguard-deploy
sudo REPO_URL="https://github.com/masterkush808/phoenixguard-private-backup-20260507.git" \
  BRANCH="main" \
  DOMAIN="phoenixguard.example.com" \
  CLOUDFLARED_TOKEN="<cloudflare-tunnel-token>" \
  FRAME_INGEST_TOKEN="<long-random-secret>" \
  FRAME_INGEST_SIGNING_SECRET="<separate-long-random-signing-secret>" \
  bash Developer/deployment/linux_cloud_brain_bootstrap.sh
```

Asset package from the current development machine:

```powershell
.\Developer\deployment\package_cloud_assets.ps1
```

Upload that zip to the VPS, then restore:

```bash
sudo bash /opt/phoenixguard/phoenixguard/Developer/deployment/restore_cloud_assets.sh /tmp/phoenixguard_cloud_assets.zip
```

Run an edge chart feed from the machine that owns chart pixels:

```powershell
python .\Developer\deployment\edge_frame_agent.py `
  --base-url "https://phoenixguard.example.com" `
  --session-id "edge-live" `
  --token "<long-random-secret>" `
  --signing-secret "<separate-long-random-signing-secret>" `
  --source-id "user-001-edge" `
  --symbol "EURCAD" `
  --timeframe "M5" `
  --bbox "80,140,1520,920" `
  --interval-sec 15
```

### Managed Windows Worker

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
  -CaptureIntervalSec 1 `
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

For Cloudflare, use the security-as-code template before public traffic:

```text
Developer/deployment/cloudflare_security/
```

Minimum Cloudflare controls:

```text
Access for dashboard/admin
Service token or mTLS-capable feed agents for frame-ingest
WAF custom rules
rate limits on /v1/mobile/frame-ingest/*
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
Require signed frame-ingest uploads before accepting worldwide feeds.
Use fresh package TTLs for MT4.
Fail closed if source lock, frame freshness, or bridge heartbeat fails.
Keep production runtime separate from dev/training environments.
```

## Deployment Readiness Checklist

```text
repo is pushed to main
.venv-live installs from requirements/locks/live-linux-py311.txt on Ubuntu VPS hosts and requirements/locks/live-win-py311.txt on Windows workers
PhoenixGuard health endpoint returns ok
single API listener on 8793
tracker captures fresh frames every configured interval
compact live state responds under normal load
package reporter is running
MT4 bridge is running only if intended
Cloudflare/Tailscale tunnel is authenticated
public URL requires authentication
watchdog service/task is enabled and writes health/restart evidence to runtime/live/logs_live
VM snapshot is configured
```
