# PhoenixGuard Universal Frame Feed Workflow

PhoenixGuard must not be tied to one Windows browser. The production pattern is:

```text
chart source on user device
-> frame feed adapter
-> HTTPS frame-ingest API
-> PhoenixGuard cloud brain
-> overlays/model council/playbook/session dashboard
```

The server is the brain. The user device is only the chart-pixel supplier.

## Source Of Truth Contract

Every frame feed must provide:

```text
session_id
source_id
source_type
source_url
symbol
timeframe
sequence_id
frame_id
capture_epoch_ms
image frame
metadata_json
```

The backend rejects frames that are:

```text
too old
from the future
too small
too large
not readable as an image
outside the scoped token permissions
repeated with a non-advancing capture timestamp
```

The normal tracker pipeline then treats the uploaded frame as the current chart surface for that session.

## Server Runtime

The VPS runs:

```text
phoenixguard-cloud-brain.service
PHOENIXGUARD_FRAME_INGEST_TOKEN or PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY
PHOENIXGUARD_FRAME_INGEST_MAX_SOURCE_AGE_SEC=180
Cloudflare Tunnel public HTTPS access
```

Use a scoped token registry for users:

```text
Developer/deployment/frame_ingest_token_registry.example.json
```

Set:

```text
PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY=/etc/phoenixguard/frame_ingest_token_registry.json
PHOENIXGUARD_FEED_TOKEN_USER001=<secret>
```

The registry limits each token to allowed session prefixes, source IDs, symbols, and timeframes.

## PC Feed Agent

The serious production feed for launch is the PC screen/chart-region agent:

```powershell
python Developer/deployment/edge_frame_agent.py `
  --config Developer/deployment/frame_feed_profiles.example.json `
  --profile desktop-pocket-m5 `
  --token "<user feed token>"
```

Or:

```powershell
Developer/deployment/run_pc_frame_feed.ps1 -Profile desktop-pocket-m5 -Token "<user feed token>"
```

The user keeps their chart visible. The agent captures only the configured chart region and pushes it every 15 seconds.

## Mobile/PWA Feed

The browser uploader is available at:

```text
/v1/mobile/frame-ingest/mobile-uploader
```

It supports:

```text
manual screenshot upload
camera/file input
best-effort browser screen capture where supported
same token/session/source contract as the PC agent
```

This is useful for testing and manual mobile feed capture. It is not the final unattended phone feed.

## Native Mobile Apps

The later Android/iOS apps should not invent a new PhoenixGuard path. They must post to:

```text
POST /v1/mobile/frame-ingest/sessions/{session_id}/frames
```

Android should use a native screen-capture/feed adapter. iOS should use a ReplayKit-style capture adapter. Both must preserve the same frame metadata and source-lock contract.

## Fair Usage For The Cheap VPS

Start with:

```text
1 active feed per user
1-3 total active live feeds on the starter VPS
15 second minimum feed interval
dashboard viewers are not counted as active feeds
```

Scale only after measuring:

```text
CPU
RAM
model latency
queue delay
frame age
stale rejection rate
dashboard latency
```

## Anti-Staleness Rules

Use one source identity per chart feed:

```text
user001-desktop-chart
user001-mobile-pwa
user001-android-native
```

Use one lifecycle sequence per chart run:

```text
user001-pocket-m5-live
user001-tradingview-audusd-m5-20260707
```

When a user changes broker, pair, timeframe, or chart region:

```text
start a new sequence_id
start or clear the session
do not reuse old overlays as current truth
```

This keeps the universal feed flexible without reopening the stale-frame problems from the local locked-window era.
