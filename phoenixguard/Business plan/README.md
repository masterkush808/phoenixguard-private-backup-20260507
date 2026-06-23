# PhoenixGuard Business Plan Architecture

This folder is the commercial operating blueprint for turning PhoenixGuard into a paid Analyst Expert Advisor service.

PhoenixGuard must be positioned as decision-support and execution-assist software. It does not guarantee profit. Markets are risky. Customers remain responsible for broker choice, account funding, lot sizing preferences, and all trading outcomes.

## Operating Principle

Do not ship the PhoenixGuard intelligence core to customers.

Ship only:

- MT4 Expert Advisor.
- Signed PhoenixGuard Connector for Windows.
- License/account binding.
- Clear onboarding, risk disclosure, and support documentation.

Keep server-side:

- Model Council.
- tracker workers.
- customer records.
- subscription status.
- entitlement logic.
- packet signing keys.
- anti-abuse checks.
- telemetry and audit records.

## Folder Map

- `00_business_operating_model.md` - product positioning, revenue model, regulatory posture, and rollout stages.
- `01_cloud_hosting_architecture.md` - where PhoenixGuard can run away from your PC and how to scale it.
- `02_security_and_license_architecture.md` - license enforcement, signed command packets, customer binding, and threat model.
- `03_customer_onboarding_automation.md` - customer signup, payment, broker-account binding, email delivery, and expiry handling.
- `04_risk_disclosures_and_terms_skeleton.md` - market-risk disclosures and terms skeleton for attorney review.
- `05_website_and_api_blueprint.md` - customer/admin portal and backend API architecture.
- `automation/email_templates.md` - transactional email templates for onboarding, renewal, expiry, and risk disclosure.
- `automation/release_and_delivery_pipeline.md` - EA and connector release automation.
- `checklists/launch_readiness_checklist.md` - go-live gates.
- `config/business_stack.example.yaml` - environment and service configuration skeleton.
- `schemas/phoenixguard_business_schema.sql` - PostgreSQL data model skeleton.
- `schemas/openapi-business-skeleton.yaml` - API contract skeleton.

## Source Anchors

This plan is aligned with the current repo flow:

- local execution packet endpoint: `phoenixguard/mobile_api/app.py`
- MT4 file bridge: `tools/phoenixguard_mt4_file_bridge.py`
- MT4 EA: `mt4/PhoenixGuard_MT4_Executioner.mq4`
- VM/Cloudflare deployment guide: `deploy/windows/WINDOWS_VM_CLOUDFLARE_TUNNEL.md`

External references used for this architecture:

- Stripe subscriptions and billing portal: https://docs.stripe.com/subscriptions
- Stripe subscription webhooks: https://docs.stripe.com/billing/subscriptions/webhooks
- Cloudflare Tunnel and WAF rate limiting: https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/ and https://developers.cloudflare.com/waf/rate-limiting-rules/
- OWASP API Security Top 10: https://owasp.org/www-project-api-security/
- NFA CTA registration overview: https://www.nfa.futures.org/registration-membership/who-has-to-register/cta.html
- CFTC CTA overview: https://www.cftc.gov/IndustryOversight/Intermediaries/CTAs/index.htm

