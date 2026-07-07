# PhoenixGuard Cloudflare Security Template

This directory contains the security posture PhoenixGuard should use before a
public deployment is exposed.

It is intentionally parameterized. Do not commit real Cloudflare account IDs,
zone IDs, tunnel tokens, service-token secrets, or admin emails.

## Required Edge Controls

```text
Cloudflare Access
  - dashboard/admin protected by human identity policy
  - frame-ingest protected by service token or mTLS-capable feed agents

Cloudflare WAF
  - managed rules enabled
  - frame-ingest endpoint constrained to POST
  - oversized/non-image abuse rate-limited at the edge
  - admin paths blocked unless Access identity is present

Cloudflare Tunnel
  - no direct public VPS API port
  - public hostname routes only through cloudflared
```

## Apply Flow

```bash
cd Developer/deployment/cloudflare_security
cp terraform.tfvars.example terraform.tfvars
# edit terraform.tfvars with real IDs and hostnames
terraform init
terraform plan
terraform apply
```

The VPS still needs the local PhoenixGuard controls:

```text
PHOENIXGUARD_FRAME_INGEST_REQUIRE_SIGNATURE=1
PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY=/etc/phoenixguard/frame_ingest_token_registry.json
PHOENIXGUARD_TRUSTED_HOSTS=<dashboard-domain>,127.0.0.1,localhost
PHOENIXGUARD_ALLOWED_ORIGINS=https://<dashboard-domain>
```

## Why Both Cloudflare And App Security

Cloudflare blocks broad internet abuse before it reaches the VPS. PhoenixGuard
still verifies scoped tokens, HMAC signatures, nonces, source locks, image
limits, and object ownership because edge controls are not a substitute for
server-side authorization.
