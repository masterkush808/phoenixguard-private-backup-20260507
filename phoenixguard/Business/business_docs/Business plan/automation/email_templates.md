# Email Templates

Use transactional email through a provider such as Resend, SendGrid, or AWS SES. Keep email links signed and expiring. Do not attach private keys or secrets.

## Welcome

Subject: Welcome to PhoenixGuard

Body:

Hello {{customer_name}},

Your PhoenixGuard account has been created.

PhoenixGuard is an analytical trading-assistance and execution-automation tool. It does not guarantee profit. Trading involves substantial risk and may result in losses.

Next steps:

1. Complete your risk disclosure acknowledgement.
2. Add your MT4 account number and broker server.
3. Complete subscription checkout.
4. Download the PhoenixGuard Connector and MT4 Expert Advisor.

Portal: {{portal_url}}

## Risk Disclosure Accepted

Subject: PhoenixGuard risk disclosure accepted

Body:

Hello {{customer_name}},

This confirms that you accepted PhoenixGuard risk disclosure version {{disclosure_version}} on {{accepted_at}}.

Summary:

- PhoenixGuard does not guarantee profit.
- Markets are risky.
- You are responsible for all trading outcomes.
- You should test in demo or dry-run mode before live use.

## License Activated

Subject: PhoenixGuard license activated

Body:

Hello {{customer_name}},

Your PhoenixGuard license is active.

License: {{license_id}}
Plan: {{plan_name}}
Broker server: {{broker_server}}
MT4 account: {{masked_account_number}}
Expiry / renewal: {{current_period_end}}

Download your setup package:

{{signed_download_url}}

This link expires on {{download_expires_at}}.

## Setup Instructions

Subject: PhoenixGuard setup instructions

Body:

Hello {{customer_name}},

Follow these steps:

1. Install PhoenixGuard Connector.
2. Copy or install the PhoenixGuard MT4 Expert Advisor.
3. Open MT4 and attach the EA to the intended chart.
4. Confirm automated trading is enabled.
5. Start with dry-run or demo validation.
6. Enable live mode only after you understand the risk settings.

PhoenixGuard does not require your broker password.

## Device Registered

Subject: PhoenixGuard device registered

Body:

Hello {{customer_name}},

A device was registered for your PhoenixGuard license.

Device: {{device_label}}
Connector version: {{connector_version}}
Registered at: {{registered_at}}

If this was not you, open the portal and revoke the device immediately.

## Payment Failed

Subject: PhoenixGuard payment failed

Body:

Hello {{customer_name}},

Your latest PhoenixGuard payment failed.

Unless payment is updated, your license may stop receiving executable commands after the grace period.

Update billing:

{{billing_portal_url}}

## License Expired

Subject: PhoenixGuard license expired

Body:

Hello {{customer_name}},

Your PhoenixGuard license has expired.

The connector will stop writing executable BUY/SELL command packets while the license is inactive. You may still see status files such as `LICENSE_EXPIRED`.

Renew here:

{{billing_portal_url}}

## Update Required

Subject: PhoenixGuard update required

Body:

Hello {{customer_name}},

A PhoenixGuard update is required for security or compatibility.

Current version: {{current_version}}
Required version: {{required_version}}

Download:

{{signed_download_url}}

The connector may stop receiving executable commands until the update is installed.

