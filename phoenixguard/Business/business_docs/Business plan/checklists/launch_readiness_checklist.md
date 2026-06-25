# Launch Readiness Checklist

## Legal And Compliance

- attorney reviewed risk disclosure.
- attorney reviewed terms of service.
- privacy policy published.
- refund/cancellation policy published.
- jurisdiction restrictions decided.
- no guaranteed-profit marketing.
- disclosure acceptance stored before download.
- support contact published.

## Payment

- payment provider account approved.
- production webhooks configured.
- webhook signatures verified.
- subscription lifecycle tested.
- failed payment flow tested.
- cancellation flow tested.
- renewal flow tested.
- tax/accounting process reviewed.

## Security

- no private keys in EA.
- no payment secrets in connector.
- command packets signed.
- EA verifies signature.
- connector verifies API TLS.
- device registration implemented.
- license revocation implemented.
- rate limits configured.
- admin MFA enabled.
- audit logging enabled.
- backups tested.
- incident response contact defined.

## Product

- EA compiles.
- connector installer signed.
- setup guide complete.
- dry-run mode tested.
- live mode tested on demo/micro account.
- expired license blocks execution.
- update-required blocks execution.
- market closed message tested.
- duplicate packet rejected.
- stale packet rejected.
- JSON tamper rejected.

## Operations

- admin dashboard usable.
- support workflow ready.
- release pipeline documented.
- rollback path documented.
- uptime monitoring configured.
- alerting configured.
- customer email templates configured.
- domain email authentication configured.
- knowledge base created.

## Private Beta Exit

- at least 2 weeks stable connector uptime.
- no unhandled stale packet executions.
- no unsigned command acceptance.
- customer onboarding works without manual intervention.
- payment expiry stops executable command delivery.
- customer can uninstall connector.

