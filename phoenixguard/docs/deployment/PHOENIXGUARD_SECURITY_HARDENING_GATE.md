# PhoenixGuard Security Hardening Gate

This gate must pass before PhoenixGuard is exposed publicly.

## Current Security Architecture

```text
Cloudflare Access/WAF/rate limits
  -> Cloudflare Tunnel
  -> VPS localhost PhoenixGuard API
  -> signed frame-ingest contract
  -> V3 runtime/playbook contracts
  -> business entitlement and MT4 command contracts
```

## Required App Controls

```text
Frame ingest:
- scoped bearer token
- token registry with per-user/session/source/symbol/timeframe scope
- HMAC-SHA256 frame signature
- timestamp skew limit
- nonce replay cache
- image allowlist and decoder verification
- pixel and byte limits
- metadata size limit
- security audit JSONL

API/dashboard:
- TrustedHostMiddleware
- configured CORS origins
- no public raw port
- no dashboard state as execution authority
- no stale/expired packet accepted as live truth

Business:
- customer/admin role separation
- object-level ownership checks
- email verification before checkout/login
- disclosure and broker binding gates
- connector device token and freshness gates
- admin-only internal family lifetime license grant
```

## Required Cloudflare Controls

```text
Cloudflare Access:
- dashboard/admin only for approved operators
- service-token or mTLS-capable feed agent access for machine feeds

Cloudflare WAF:
- block non-POST frame-ingest methods
- block admin paths without Access identity
- rate limit /v1/mobile/frame-ingest/*

Cloudflare Tunnel:
- origin API listens on 127.0.0.1
- VPS firewall does not expose the API port
```

The template is in:

```text
Developer/deployment/cloudflare_security/
```

## Required VPS Controls

```text
systemd service:
- non-root PhoenixGuard user
- NoNewPrivileges=true
- UMask=0077
- runtime-only write path
- automatic restart

watchdog:
- probes /v1/mobile/health
- probes /v1/mobile/frame-ingest/readiness
- probes live state when session is configured
- restarts the cloud-brain service after repeated failures

asset integrity:
- generate model/runtime asset manifest before migration
- verify manifest on VPS before service launch
```

## Internal Family Lifetime Control

Family/unpaid lifetime access is not a public plan. It is:

```text
plan_code: internal-family-lifetime
public_visible: false
self_service: false
admin endpoint only:
POST /v1/admin/customers/{customer_id}/family-lifetime-license
```

It still respects:

```text
risk disclosure
broker binding
device registration
device freshness
command freshness
runtime safety
```

## Release Commands

```text
python Developer/deployment/verify_release_readiness.py
python -m pyright
python -m pip check
python Backend/tools/verify_v3_integrity.py
python -m pytest Backend/tests/test_deployment_release_contract.py Backend/tests/test_frame_ingest_api.py Backend/tests/test_business_commercial_api.py -q
```

## Non-Negotiables

```text
Do not expose localhost:8793 publicly.
Do not deploy unsigned frame ingest.
Do not commit feed tokens, signing secrets, Cloudflare tunnel tokens, VPS keys, or broker credentials.
Do not bypass object-level authorization with session_id alone.
Do not let MT4 consume unsigned, stale, expired, or non-entitled commands.
```
