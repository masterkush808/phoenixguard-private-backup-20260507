# Security And License Architecture

## Core Security Rule

The client is hostile by default.

Anything on the user's PC can be copied, inspected, patched, replayed, or automated against. Therefore, PhoenixGuard security must be server-authoritative.

## Commercial Components

```text
EA
  public key only
  local risk controls
  reads signed JSON

Connector
  authenticated device token
  writes MT4 Common Files
  no private signing keys

Cloud
  private signing keys
  entitlement decisions
  tracker intelligence
  customer/license records
```

## License Binding

Bind each license to:

- customer ID.
- license ID.
- plan ID.
- MT4 account number hash.
- broker server hash.
- device ID.
- connector build channel.
- expiry timestamp.
- revocation status.

The broker account number and broker server are not secrets. Treat them as identifiers, not passwords.

## Signed Command Packet

Use asymmetric signing.

- Server signs command with private key.
- EA/connector verify command with public key.
- Never use a shared HMAC secret embedded in the EA because it can be extracted.

Recommended packet fields:

```json
{
  "schema_version": "PG_MT4_EXECUTION_COMMAND_V2",
  "license_id": "lic_x",
  "customer_id": "cus_x",
  "account_number_hash": "sha256:...",
  "broker_server_hash": "sha256:...",
  "packet_id": "pgpkt_x",
  "stream_sequence": 123,
  "side": "BUY",
  "symbol": "EURAUD",
  "timeframe": "H1",
  "created_epoch": 1781980000,
  "valid_until_epoch": 1781980002,
  "risk_profile": "server-default-local-ea-enforced",
  "signature_alg": "Ed25519",
  "signature": "base64url..."
}
```

## EA Rejection Rules

The EA rejects when:

- signature invalid.
- schema version unknown.
- license ID missing.
- account/server binding mismatch.
- packet expired.
- packet too old.
- packet sequence replayed.
- side not BUY or SELL.
- duplicate packet ID.
- market closed.
- trade disabled.
- spread too high.
- max open trades reached.
- daily loss limit reached.
- equity drawdown stop reached.
- cooldown active.
- lot sizing cannot pass broker constraints.

## Connector Rejection Rules

The connector rejects or stops writing executable commands when:

- subscription inactive.
- device revoked.
- license revoked.
- broker account not approved.
- API token expired.
- command signature invalid.
- local MT4 Common Files path unavailable.
- remote service unavailable.

Connector may write status files:

- `NO_EXECUTION_PACKET`
- `LICENSE_EXPIRED`
- `DEVICE_REVOKED`
- `ACCOUNT_NOT_BOUND`
- `SERVICE_UNAVAILABLE`
- `UPDATE_REQUIRED`

## Threat Model

| Threat | Defense |
| --- | --- |
| Customer edits JSON to force BUY/SELL | EA verifies command signature |
| Customer copies EA to friend | account/server/license binding fails |
| Customer copies connector token | device binding, heartbeat anomaly, revoke token |
| Customer replays old good packet | short TTL, sequence, packet ID memory |
| Customer patches EA | server still controls signed commands; high-value plans can use native DLL verification and build watermarking |
| API endpoint enumeration | auth on every endpoint, object-level authorization, rate limits |
| stolen admin password | MFA, Cloudflare Access, admin RBAC, audit log |
| database leak | hashed account identifiers, encrypted secrets, least privilege |
| signing key leak | rotate key, revoke affected build channel, publish new public key |
| payment webhook spoof | verify provider webhook signatures |
| DDoS/brute force | Cloudflare WAF/rate limiting |

## API Security Rules

- Never trust `customer_id`, `license_id`, `session_id`, or `account_id` from a request without checking ownership.
- Every endpoint must enforce object-level authorization.
- Admin APIs must be separated from connector APIs.
- Webhook endpoints must verify payment provider signatures.
- All tokens must have short expiry and rotation.
- Device refresh tokens must be revocable.
- Log enough to investigate fraud, but do not log broker passwords or card data.

## Secret Handling

Keep out of the EA and connector:

- database passwords.
- payment provider secret keys.
- signing private keys.
- model weights that represent proprietary strategy.
- admin credentials.

Allowed in the EA:

- public verification key.
- public API base URL.
- build channel.
- default risk settings.

