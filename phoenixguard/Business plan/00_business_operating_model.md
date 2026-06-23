# Business Operating Model

## Product Definition

PhoenixGuard Commercial is an Analyst Expert Advisor platform:

- It analyzes market conditions.
- It may deliver BUY/SELL execution commands to a customer-controlled MT4 terminal.
- It does not guarantee profit.
- It does not manage customer funds.
- It must not collect broker passwords.
- It must not promise accuracy, returns, drawdown limits, or account growth.

Customer-facing wording:

> PhoenixGuard is an analytical trading-assistance and execution-automation tool. Trading involves substantial risk and may result in losses, including total loss of deposited funds. PhoenixGuard does not guarantee profit or trading success. You are solely responsible for using the software, configuring risk controls, choosing brokers, and accepting all trade outcomes.

## Commercial Shape

Recommended subscription tiers:

| Tier | Purpose | Controls |
| --- | --- | --- |
| Trial | limited validation | dry-run/paper mode, short expiry, strict rate limits |
| Standard | single MT4 account | one broker server/account binding, one device, normal packet access |
| Pro | serious user | two devices, priority connector updates, stronger telemetry |
| Managed Desk | business client | custom onboarding, signed agreement, direct compliance review |

Avoid lifetime licenses. Monthly or annual subscriptions keep control server-side.

## What Users Provide

Collect only the minimum needed:

- legal name or business name.
- email.
- billing country.
- phone optional.
- MT4 account number.
- broker server name.
- selected plan.
- risk disclosure acceptance timestamp.
- optional device name.

Never request:

- broker password.
- investor password.
- card data directly.
- seed phrases.
- remote desktop credentials.
- exchange API secrets unless a future product explicitly requires them and has a separate security model.

## Revenue Workflow

1. User lands on website.
2. User reads risk disclosure.
3. User creates account.
4. User enters broker server and MT4 account number.
5. User selects plan.
6. Payment provider creates subscription.
7. Webhook marks entitlement active.
8. System issues license and connector download.
9. Email sends setup instructions and EA package link.
10. Connector authenticates, binds device, and starts receiving signed commands.

## Regulatory Posture

This product can fall near regulated financial-advice territory, especially if marketed as trade recommendations or automated execution. In the US, the NFA describes a Commodity Trading Advisor as a person or organization that, for compensation or profit, advises others on trading futures, options on futures, retail off-exchange forex, or swaps. The CFTC points CTA registration details to the NFA.

Operating posture before public launch:

- attorney review before taking public subscriptions.
- country restrictions until reviewed.
- no guaranteed profit claims.
- no performance claims without evidence and disclaimers.
- no screenshots implying guaranteed outcomes.
- no "risk-free" wording.
- explicit acknowledgement before software download.
- audit trail of every disclosure accepted.

## Rollout Stages

### Stage 1: Founder Controlled Burn

- current local tracker.
- bridge stays local.
- no paying users.
- validate EA safety, packet freshness, and audit logs.

### Stage 2: Private Beta

- 5 to 10 trusted testers.
- dry-run first, then micro-lot live only.
- manually reviewed accounts.
- signed installer.
- Stripe/Paddle test mode.
- explicit signed risk acknowledgement.

### Stage 3: Paid Controlled Launch

- payment webhooks active.
- license server active.
- signed command packets.
- connector auto-update.
- admin dashboard.
- customer support workflow.

### Stage 4: Scale

- cloud GPU tracker workers.
- queue/fanout service.
- multi-region CDN/API edge.
- formal compliance program.
- dedicated incident response.

