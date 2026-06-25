# Website And API Blueprint

## Website Modules

### Public Site

- product overview.
- risk-first positioning.
- pricing.
- FAQ.
- legal/risk disclosure.
- contact/support.
- login/signup.

The public website must avoid exaggerated performance claims.

### Customer Portal

- profile.
- broker account binding.
- subscription status.
- license status.
- device list.
- EA/connector downloads.
- setup checklist.
- risk settings defaults.
- disclosure acceptance history.
- support tickets.

### Admin Portal

- customer search.
- subscriptions.
- licenses.
- devices.
- account bindings.
- entitlements.
- connector heartbeat.
- command delivery logs.
- revocations.
- release management.
- email resend.
- audit log.

## Backend Services

```text
web-app
  public pages + customer portal

api-gateway
  auth, rate limits, request validation

billing-service
  payment provider webhooks, subscription state

license-service
  entitlement resolution, account/device binding

connector-service
  command polling, heartbeat, release checks

packet-gateway
  receives approved Model Council packets, signs command packets

tracker-workers
  private PhoenixGuard model/tracker infrastructure

admin-service
  internal operations, revocation, audit
```

## Core APIs

Customer:

- `POST /v1/public/checkout/start`
- `POST /v1/auth/login`
- `GET /v1/me`
- `POST /v1/disclosures/accept`
- `POST /v1/broker-accounts`
- `GET /v1/licenses`
- `GET /v1/releases/latest`

Connector:

- `POST /v1/device/register`
- `POST /v1/device/heartbeat`
- `GET /v1/entitlements/current`
- `GET /v1/commands/latest`
- `GET /v1/releases/connector/latest`

Billing:

- `POST /v1/webhooks/stripe`
- `POST /v1/webhooks/paddle`

Admin:

- `GET /v1/admin/customers`
- `GET /v1/admin/customers/{customer_id}`
- `POST /v1/admin/licenses/{license_id}/revoke`
- `POST /v1/admin/releases`
- `GET /v1/admin/audit-events`

## Object-Level Authorization

Every request must check ownership. A user cannot access a license, account, device, command, or support ticket merely by guessing an ID.

Mandatory check:

```text
authenticated principal
  -> customer_id
  -> owns requested license/device/account
  -> plan permits action
  -> subscription active
  -> disclosure accepted
```

## Command Delivery Logic

`GET /v1/commands/latest` returns one of:

- signed executable command.
- `NO_EXECUTION_PACKET`.
- `LICENSE_EXPIRED`.
- `DEVICE_REVOKED`.
- `UPDATE_REQUIRED`.
- `ACCOUNT_NOT_BOUND`.
- `SERVICE_UNAVAILABLE`.

Never return unsigned executable BUY/SELL commands.

## Website Data Capture

Store:

- user profile.
- billing provider customer ID.
- subscription status.
- accepted disclosure version.
- MT4 account number hash.
- broker server hash.
- license status.
- device status.
- connector version.
- release version downloaded.
- command delivery audit.

Do not store:

- broker password.
- card number.
- MT4 terminal password.
- private signing keys.

## AI Leverage

Use AI safely for:

- support ticket triage.
- customer onboarding assistant.
- setup guide generation.
- log summarization.
- anomaly detection in connector heartbeats.
- fraud/risk scoring for shared licenses.
- admin search over audit logs.

Do not let AI:

- override subscription state.
- bypass risk disclosures.
- approve live execution without deterministic entitlement checks.
- modify signed command packets after signing.
- expose backend prompts, model internals, or customer data to unauthorized users.

