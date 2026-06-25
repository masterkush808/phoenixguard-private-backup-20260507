# Customer Onboarding Automation

## Goal

The customer should move from payment to licensed setup without manual work from you, while still accepting risk disclosures and providing only non-sensitive broker identifiers.

## Automated Flow

```text
Website signup
  -> risk disclosure acceptance
  -> broker server + MT4 account number
  -> subscription checkout
  -> webhook activates entitlement
  -> license created
  -> release package link generated
  -> onboarding email sent
  -> customer installs connector and EA
  -> connector registers device
  -> connector starts heartbeat
  -> EA reads signed command file
```

## Required Forms

### Signup

- full name.
- email.
- country.
- phone optional.
- passwordless login or account password.
- consent to terms.
- consent to risk disclosure.

### Broker Binding

- MT4 account number.
- broker server name.
- broker display name optional.
- MT4 chart symbol suffix notes optional, such as `EURAUDm`.

Do not collect broker passwords.

### Risk Settings

Keep final risk enforcement in the EA, but allow the portal to store defaults:

- default dry-run/live preference.
- max daily trades.
- risk percent cap.
- max spread.
- daily loss cap.
- max equity drawdown cap.
- max open positions.

## Payment Automation

Use payment webhooks to update entitlement:

| Payment Event | System Action |
| --- | --- |
| subscription created | create pending license |
| invoice paid | activate entitlement |
| trial ending | send reminder |
| payment failed | mark grace period and email customer |
| subscription past due | connector writes warning status |
| subscription canceled | entitlement expires at period end |
| chargeback/fraud | revoke license immediately |

## Email Automation

Transactional emails:

- welcome and risk disclosure copy.
- payment receipt.
- license activated.
- EA and connector download.
- setup guide.
- device registered.
- renewal reminder.
- payment failed.
- license expired.
- update required.
- revocation/security notice.

Email provider options:

- Resend for developer-friendly transactional email.
- SendGrid for high-volume email API.
- AWS SES if already running inside AWS and comfortable with domain verification.

## Delivery Method

Do not attach the EA directly to every email if possible. Send a signed, expiring download link.

Release package should include:

- compiled EA.
- PhoenixGuard Connector installer.
- checksums.
- setup guide PDF/HTML.
- current risk disclosure.
- version manifest.

## Expiry Behavior

When subscription expires:

1. License server marks entitlement inactive.
2. Connector receives `LICENSE_EXPIRED`.
3. Connector stops writing executable BUY/SELL commands.
4. Connector writes status JSON only.
5. EA logs `license expired` or `no executable packet`.
6. Portal shows renewal button.

Do not require resending a new EA for monthly renewal. Renew entitlement server-side.

## Support Workflow

Customer support needs:

- customer search by email.
- license status.
- broker server/account binding.
- device heartbeat status.
- last command delivery status.
- last EA audit upload optional.
- revoke button.
- resend setup email button.
- force update button.

## Data Retention

Minimum suggested retention:

- payment records: according to accounting/tax requirements.
- risk disclosure acceptance: lifetime of account plus legal retention period.
- command delivery logs: 90 to 365 days.
- connector heartbeat: 30 to 90 days.
- support messages: 1 to 3 years.

Confirm retention with legal and accounting counsel before launch.

