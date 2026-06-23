import { expect, test } from 'playwright/test';

const runE2E = process.env.BUSINESS_E2E === '1';
const webBaseUrl = process.env.BUSINESS_WEB_BASE_URL || 'http://127.0.0.1:3210';
const apiBaseUrl = process.env.BUSINESS_API_BASE_URL || process.env.NEXT_PUBLIC_API_BASE_URL || 'http://127.0.0.1:18180';
const activeCustomerEmail = process.env.BUSINESS_E2E_CUSTOMER_EMAIL || 'customer@phoenixguard.test';
const expiredCustomerEmail = process.env.BUSINESS_E2E_EXPIRED_EMAIL || 'expired@phoenixguard.test';
const customerPassword = process.env.BUSINESS_E2E_PASSWORD || 'business-password';
const activeLicenseKey = process.env.BUSINESS_E2E_LICENSE_KEY || 'PG-REPLACE-WITH-LIVE-LICENSE';

test.describe('PhoenixGuard commercial onboarding portal flow', () => {
  test.skip(!runE2E, 'Set BUSINESS_E2E=1 and start the business API plus web dev server before running this spec.');
  test.setTimeout(90_000);

  let consoleErrors: string[] = [];

  test.beforeEach(async ({ page }) => {
    consoleErrors = [];
    page.on('console', message => {
      if (message.type() === 'error') {
        consoleErrors.push(message.text());
      }
    });
    page.on('pageerror', error => {
      consoleErrors.push(error.message);
    });
  });

  test.afterEach(async () => {
    expect(consoleErrors, `Unexpected browser console/page errors:\n${consoleErrors.join('\n')}`).toEqual([]);
  });

  test('login, disclosure, broker bind, active license, portal, tracker GUI, refresh, relogin', async ({ page, request }) => {
    const health = await request.get(`${apiBaseUrl}/healthz`);
    expect(health.ok()).toBeTruthy();
    await expect(health.json()).resolves.toMatchObject({ status: 'ok', live_bridge_touched: false });

    await page.goto(`${webBaseUrl}/login`);
    await page.getByTestId('login-email').fill(activeCustomerEmail);
    await page.getByTestId('login-password').fill(customerPassword);
    await page.getByTestId('login-submit').click();

    await page.getByTestId('disclosure-accept-checkbox').check();
    await page.getByTestId('disclosure-accept-submit').click();

    await expect(page.getByTestId('broker-binding-form')).toBeVisible();
    await page.getByTestId('broker-server').fill('PocketOption-Demo');
    await page.getByTestId('mt4-account-number').fill('8082026');
    await page.getByTestId('broker-bind-submit').click();

    await expect(page.getByTestId('device-register-form')).toBeVisible();
    const licenseKey = page.getByTestId('license-key');
    if (!(await licenseKey.inputValue())) {
      await licenseKey.fill(activeLicenseKey);
    }
    await page.getByTestId('device-fingerprint').fill('qa-web-device');
    await page.getByTestId('device-register-submit').click();
    await expect(page.getByTestId('device-heartbeat-submit')).toBeEnabled();
    await page.getByTestId('device-heartbeat-submit').click();

    await expect(page.getByTestId('portal-shell')).toBeVisible();
    await expect(page.getByTestId('license-status')).toContainText(/active/i);

    const trackerStatus = await request.get(`${apiBaseUrl}/v1/tracker/status`);
    expect(trackerStatus.ok()).toBeTruthy();
    await expect(trackerStatus.json()).resolves.toMatchObject({
      alive: true,
      live_bridge_touched: false,
      tracking_enabled: true,
    });

    await expect(page.getByTestId('open-tracker-gui')).toBeEnabled();
    await page.getByTestId('open-tracker-gui').click();
    await expect(page.getByTestId('tracker-gui')).toBeVisible();
    await expect(page.getByTestId('tracker-status')).toContainText(/alive|running/i);

    await page.reload({ waitUntil: 'networkidle' });
    await expect(page.getByTestId('portal-shell')).toBeVisible();
    await expect(page.getByTestId('license-status')).toContainText(/active/i);

    await page.getByTestId('logout-button').click();
    await page.goto(`${webBaseUrl}/login`);
    await page.getByTestId('login-email').fill(activeCustomerEmail);
    await page.getByTestId('login-password').fill(customerPassword);
    await page.getByTestId('login-submit').click();
    await expect(page.getByTestId('portal-shell')).toBeVisible();
    await expect(page.getByTestId('license-status')).toContainText(/active/i);
  });

  test('customer token is denied from admin APIs', async ({ request }) => {
    const login = await request.post(`${apiBaseUrl}/v1/auth/login`, {
      data: { email: activeCustomerEmail, password: customerPassword },
    });
    expect(login.ok()).toBeTruthy();
    const { access_token: token } = await login.json();

    const denied = await request.get(`${apiBaseUrl}/v1/admin/customers`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(denied.status()).toBe(403);
    await expect(denied.json()).resolves.toMatchObject({ detail: 'Admin access denied.' });
  });

  test('expired customer command polling fails closed', async ({ request }) => {
    const login = await request.post(`${apiBaseUrl}/v1/auth/login`, {
      data: { email: expiredCustomerEmail, password: customerPassword },
    });
    expect(login.ok()).toBeTruthy();
    const { access_token: token } = await login.json();
    const headers = { Authorization: `Bearer ${token}` };

    const disclosure = await request.post(`${apiBaseUrl}/v1/disclosures/accept`, {
      headers,
      data: { accepted: true, version: 'risk-disclosure-2026-06' },
    });
    expect(disclosure.status()).toBe(204);

    const broker = await request.post(`${apiBaseUrl}/v1/broker-accounts`, {
      headers,
      data: {
        broker_server: 'PocketOption-Demo',
        mt4_account_number: '8082026',
        label: 'QA expired account',
      },
    });
    expect(broker.status()).toBe(201);

    const commandResponse = await request.get(`${apiBaseUrl}/v1/commands/latest`, { headers });
    expect(commandResponse.ok()).toBeTruthy();
    const commandPayload = await commandResponse.json();
    expect(commandPayload.status).toBe('LICENSE_EXPIRED');
    expect(commandPayload.command.execution_authority).toBe(false);
    expect(commandPayload.command.side).toBeUndefined();
  });
});
