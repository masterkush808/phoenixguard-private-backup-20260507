# Cloud Hosting Architecture

## Recommended Hosting Strategy

PhoenixGuard currently has Windows-centric live tracking and local MT4 bridge behavior. The safest commercial path is hybrid:

```text
Customer PC
  MT4 EA + PhoenixGuard Connector

PhoenixGuard Cloud
  website + payment + license server + signed packet gateway
  tracker workers + Model Council
  admin dashboard + telemetry + audit store
```

The customer PC executes trades through the user's already logged-in MT4 terminal. Your cloud does not need broker passwords.

## Phase 1: Production VM

Best first production host:

- Windows Server GPU VM.
- PhoenixGuard runs on localhost.
- Cloudflare Tunnel exposes only required HTTPS services.
- Cloudflare Access protects admin dashboards.
- Cloudflare WAF/rate limiting protects public APIs.
- PostgreSQL runs as managed database, not inside the app folder.
- object storage holds builds, logs, and artifact snapshots.

This matches the existing repo deployment direction in `Backend/launch/deploy/windows/WINDOWS_VM_CLOUDFLARE_TUNNEL.md`.

Why Windows first:

- current tracker is browser/window/capture heavy.
- GUI capture is simpler on Windows.
- MT4/Pocket Option style testing is easier to replicate.

## Phase 2: Split Services

Move from one VM to service separation:

```text
Cloudflare Edge
  -> Web App / Customer Portal
  -> API Gateway
  -> License Service
  -> Packet Gateway
  -> Tracker Worker Queue
  -> GPU Tracker Workers
  -> Postgres
  -> Redis/NATS
  -> Object Storage
```

Use queues so tracker and billing failures do not block the public website.

## Phase 3: GPU Worker Fleet

When load grows:

- keep customer portal/API on normal CPU nodes.
- run model inference on GPU nodes.
- publish only compact signed execution commands to clients.
- reserve one or more warm GPU workers for live sessions.
- keep cold workers for slower analysis/backtesting jobs.

Possible platforms:

- AWS EC2 accelerated computing instances for stable production GPU servers.
- Azure NC GPU VMs for Windows or enterprise Microsoft stack alignment.
- Google Cloud GPU VMs for Linux/container inference.
- RunPod Serverless for bursty containerized inference workloads.

Use serverless GPU only after PhoenixGuard's tracker path is containerized and no longer depends on a live Windows desktop.

## Service Boundaries

| Service | Responsibility | Internet Exposure |
| --- | --- | --- |
| Website | marketing, login, checkout | public |
| Customer API | license, onboarding, downloads | public with auth |
| Connector API | heartbeat, command polling, release check | public with device auth |
| Admin API | customers, revocations, telemetry | private behind Access/VPN |
| Tracker Worker | Model Council, packet generation | private |
| Signing Service | command signatures | private only |
| Database | customer and entitlement state | private only |
| Object Storage | release bundles and logs | signed URLs only |

## Data Flow

```text
Model Council publishes PG_EXECUTION_PACKET_V3
  -> Packet Gateway checks active entitlement
  -> Command minimized for MT4
  -> Command signed by private key
  -> Connector downloads command
  -> Connector writes MT4 Common Files JSON
  -> EA validates signature/freshness/account binding
  -> EA applies local risk rules
  -> EA sends broker order from customer terminal
```

## Availability Rules

- Fail closed when entitlement cannot be checked.
- Fail closed when command signature cannot be verified.
- Fail closed when packet is stale.
- Fail closed when tracker worker health is degraded.
- Connector writes `NO_EXECUTION_PACKET`, `LICENSE_EXPIRED`, or `SERVICE_UNAVAILABLE`; it must not write fake BUY/SELL.

## Infrastructure Security

- All public traffic over HTTPS.
- Cloudflare WAF and rate limiting at edge.
- No public database ports.
- Cloud VM firewalls allow only Cloudflare tunnel or private VPN.
- Secrets stored in cloud secret manager.
- private signing key stored in KMS/HSM-backed storage where possible.
- regular backups with restore tests.
- release artifacts signed and checksummed.

