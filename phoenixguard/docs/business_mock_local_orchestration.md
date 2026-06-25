# Business Mock Local Orchestration

This is the local integration lane for the commercial mock portal and business API. It is intentionally separate from the live tracker bridge, `shooter.py`, and MT4 file bridge.

## Boundaries

- Mock API: `Business/api/business_mock_api.py`
- Mock stack launcher: `Business/api/start_business_mock_local.ps1`
- Playwright contract: `Business/web/tests/e2e/business-mock-flow.spec.ts`
- Business API pytest: `Backend/tests/test_business_integration_mock_api.py`

The launcher uses default ports `18180` for FastAPI and `3210` for Next.js. It does not start `shooter.py`, `Backend/tools/phoenixguard_mt4_file_bridge.py`, live broker automation, or the mobile API bridge ports.

## Mock Accounts

| Role | Email | Password | Expected Gate |
| --- | --- | --- | --- |
| Active customer | `customer@phoenixguard.test` | `mock-password` | Disclosure + broker binding unlock active test license |
| Expired customer | `expired@phoenixguard.test` | `mock-password` | Command polling returns `LICENSE_EXPIRED` only |
| Admin | `admin@phoenixguard.test` | `mock-password` | Admin API allowed; customer command polling denied |

Use broker server `PocketOption-Demo` and MT4 account `8082026` for the happy path.

## Start And Stop

```powershell
.\Business\api\start_business_mock_local.ps1 -InstallWebDeps
```

The script writes logs and PIDs under `.codex_runtime/business_mock/`.

```powershell
.\Business\api\start_business_mock_local.ps1 -Stop
```

To start only the API:

```powershell
.\Business\api\start_business_mock_local.ps1 -SkipWeb
```

## Test Commands

Business API gates:

```powershell
.\.venv\Scripts\python.exe -m pytest Backend/tests/test_business_integration_mock_api.py -v
```

Playwright E2E contract, after the mock stack is running:

```powershell
$env:BUSINESS_E2E="1"
$env:BUSINESS_WEB_BASE_URL="http://127.0.0.1:3210"
$env:BUSINESS_API_BASE_URL="http://127.0.0.1:18180"
Push-Location Business\web
npx playwright test tests/e2e/business-mock-flow.spec.ts --reporter=line
Pop-Location
```

Without `BUSINESS_E2E=1`, the Playwright spec skips. That keeps normal repo test runs from failing when the mock Next.js server is not running.

## Frontend Contract

The mock Next.js portal should expose these test IDs for the E2E lane:

| Step | Test ID |
| --- | --- |
| Login email | `login-email` |
| Login password | `login-password` |
| Login submit | `login-submit` |
| Disclosure checkbox | `disclosure-accept-checkbox` |
| Disclosure submit | `disclosure-accept-submit` |
| Broker binding form | `broker-binding-form` |
| Broker server input | `broker-server` |
| MT4 account input | `mt4-account-number` |
| Broker bind submit | `broker-bind-submit` |
| Portal shell | `portal-shell` |
| License status | `license-status` |
| Open tracker GUI | `open-tracker-gui` |
| Tracker GUI wrapper | `tracker-gui` |
| Tracker status text | `tracker-status` |
| Logout | `logout-button` |

## Final Validation Checklist

- Customer token is denied from `GET /v1/admin/customers` with HTTP `403`.
- Expired customer command polling returns `LICENSE_EXPIRED`, `execution_authority=false`, and no executable `side`.
- Refresh preserves the active customer portal session after disclosure and broker binding.
- Logout and relogin returns the active customer to the portal without requiring duplicate disclosure or broker binding.
- Tracker status reports `alive=true` and the tracker GUI renders `data-testid="tracker-gui"`.
- Browser console has no `console.error` messages and no uncaught `pageerror` events during the E2E run.
- Live bridge remains untouched: no `shooter.py`, MT4 bridge, or live broker process is started by the mock launcher.
