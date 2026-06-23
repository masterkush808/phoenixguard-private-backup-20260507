import { getApiBaseUrl } from "@/lib/tracker";

export const ACCESS_TOKEN_COOKIE = "pg_access_token";
export const CONNECTOR_TOKEN_COOKIE = "pg_connector_token";
export const DISCLOSURE_VERSION = "risk-disclosure-2026-06";

export type ApiResult<T> = {
  ok: boolean;
  status: number;
  data: T | null;
  error: string;
};

export type ApiOptions = {
  token?: string | null;
  connectorToken?: string | null;
  method?: "GET" | "POST";
  body?: unknown;
};

export type BusinessHealth = {
  status: "online" | "offline";
  mode: string;
  provider_adapters: Record<string, unknown>;
  tracker_session_id?: string;
  live_bridge_touched?: boolean;
};

export type BusinessUser = {
  id?: string;
  email: string;
  full_name?: string;
  role?: "customer" | "admin" | string;
  customer_id?: string;
  license_id?: string;
  license_key?: string;
  license_status?: string;
  status?: string;
  is_admin?: boolean;
  disclosure_accepted: boolean;
  email_verified?: boolean;
  broker_account_bound?: boolean;
  device_status?: string;
  tracker_session_id?: string;
};

export type LicensePayload = {
  id?: string;
  license_id?: string;
  license_key?: string;
  license_key_hint?: string;
  status: string;
  plan_code: string;
  expires_at_epoch: number;
  runtime_policy?: {
    daily_runtime_hours?: number;
    daily_runtime_seconds?: number;
    runtime_label?: string;
    tier?: string;
    heartbeat_freshness_seconds?: number;
    command_freshness_seconds?: number;
    stale_market_data_seconds?: number;
  };
  runtime_state?: {
    limit_seconds?: number;
    used_seconds?: number;
    remaining_seconds?: number;
    available?: boolean;
  };
  package_profile?: {
    code?: string;
    name?: string;
    tier?: string;
    price_label?: string;
    certification_level?: string;
  };
  package_certification?: {
    certification_id?: string;
    status?: string;
    level?: string;
    plan_code?: string;
    package_name?: string;
    expires_at_epoch?: number;
  };
  phoenix_guard_settings?: Record<string, unknown>;
  is_active?: boolean;
  requires_disclosure_acceptance?: boolean;
  requires_broker_account_binding?: boolean;
};

export type Entitlement = {
  status: string;
  license_id: string;
  plan_code: string;
  expires_at_epoch?: number;
  runtime_policy?: {
    daily_runtime_hours?: number;
    daily_runtime_seconds?: number;
    runtime_label?: string;
    tier?: string;
    heartbeat_freshness_seconds?: number;
    command_freshness_seconds?: number;
    stale_market_data_seconds?: number;
  };
  runtime_state?: {
    limit_seconds?: number;
    used_seconds?: number;
    remaining_seconds?: number;
    available?: boolean;
  };
  package_certification?: LicensePayload["package_certification"];
  reason?: string;
  account_bound: boolean;
  disclosure_accepted: boolean;
  subscription_status?: string;
};

export type CommandStatus = {
  status: string;
  command: {
    status: string;
    execution_authority: boolean;
    side?: string;
    symbol?: string;
    confidence?: number;
    reason?: string;
  };
};

export type ReleaseManifest = {
  id?: string;
  channel: string;
  version?: string;
  ea_version: string;
  connector_version: string;
  minimum_connector_version: string;
  download_url: string;
  sha256_manifest: string;
  status: string;
  license_status?: string;
};

export type TrackerHealth = {
  alive: boolean;
  mode: string;
  session_id?: string;
  tracking_enabled: boolean;
  live_bridge_touched?: boolean;
};

type TrackerAccessPayload = {
  allowed?: boolean;
  status?: string;
  default_session_id?: string;
  dashboard_url?: string;
};

export type PortalSnapshot = {
  apiOnline: boolean;
  health: BusinessHealth;
  user: BusinessUser | null;
  licenses: LicensePayload[];
  entitlement: Entitlement | null;
  release: ReleaseManifest | null;
  command: CommandStatus | null;
  tracker: TrackerHealth | null;
  onboarding: OnboardingStatus | null;
  errors: string[];
};

export type OnboardingStatus = {
  allowed: boolean;
  blocked_reasons: string[];
  gates: Record<string, boolean>;
  license_id?: string;
  selected_plan_code?: string;
  runtime_state?: LicensePayload["runtime_state"] | null;
  package_certification?: LicensePayload["package_certification"] | null;
  next_action?: string;
};

export type AccessGate = {
  id:
    | "registration"
    | "email-confirmation"
    | "license"
    | "disclosure"
    | "broker-binding"
    | "device-health"
    | "tracker-launch";
  label: string;
  value: string;
  detail: string;
  status: string;
  passed: boolean;
  tone: "good" | "warn" | "blocked" | "info";
};

export type AdminCustomer = BusinessUser & {
  full_name: string;
  status: string;
  license_count: number;
};

export type AdminCustomersResult = {
  ok: boolean;
  status: "ok" | "offline" | "unauthorized" | "forbidden" | "error";
  detail: string;
  customers: AdminCustomer[];
};

type HealthzPayload = {
  status: string;
  live_bridge_touched?: boolean;
  tracker_session_id?: string;
};

type LoginResponse = {
  access_token: string;
  token_type: string;
  user?: BusinessUser;
  customer?: BusinessUser;
  requires_disclosure_acceptance?: boolean;
  requires_broker_account_binding?: boolean;
};

const activeLicenseStatuses = new Set(["active", "trialing", "grace"]);

export async function apiRequest<T>(path: string, options: ApiOptions = {}): Promise<ApiResult<T>> {
  const token = options.connectorToken || options.token;
  const headers: Record<string, string> = {};

  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  try {
    const response = await fetch(`${getApiBaseUrl()}${path}`, {
      method: options.method || "GET",
      cache: "no-store",
      headers,
      body: options.body === undefined ? undefined : JSON.stringify(options.body)
    });
    const text = await response.text();
    const parsed = text ? safeJsonParse<T>(text) : null;
    const error =
      response.ok
        ? ""
        : typeof parsed === "object" && parsed && "detail" in parsed
          ? String((parsed as { detail: unknown }).detail)
          : text || `HTTP ${response.status}`;

    return {
      ok: response.ok,
      status: response.status,
      data: response.ok ? parsed : null,
      error
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      data: null,
      error: error instanceof Error ? error.message : "Service request failed."
    };
  }
}

export async function loginWithPassword(email: string, password: string) {
  return apiRequest<LoginResponse>("/v1/auth/login", {
    method: "POST",
    body: { email, password }
  });
}

export async function getBusinessHealth(): Promise<BusinessHealth> {
  const businessHealth = await apiRequest<BusinessHealth>("/v1/business/health");
  if (businessHealth.ok && businessHealth.data) {
    return {
      ...businessHealth.data,
      status: "online"
    };
  }

  const health = await apiRequest<HealthzPayload>("/healthz");

  if (!health.ok || !health.data) {
    return {
      status: "offline",
      mode: "service-unreachable",
      provider_adapters: {
        business_api: health.error || "offline"
      }
    };
  }

  return {
    status: "online",
    mode: "business-api",
    tracker_session_id: health.data.tracker_session_id,
    live_bridge_touched: health.data.live_bridge_touched,
    provider_adapters: {
      business_api: health.data.status,
      live_bridge: health.data.live_bridge_touched ? "touched" : "untouched"
    }
  };
}

export async function getPortalSnapshot({
  accessToken,
  connectorToken
}: {
  accessToken?: string | null;
  connectorToken?: string | null;
} = {}): Promise<PortalSnapshot> {
  const health = await getBusinessHealth();
  const errors: string[] = [];

  if (!accessToken) {
    return {
      apiOnline: health.status === "online",
      health,
      user: null,
      licenses: [],
      entitlement: null,
      release: null,
      command: null,
      tracker: health.status === "online" ? await getTrackerHealth(errors, accessToken || connectorToken) : null,
      onboarding: null,
      errors
    };
  }

  const [me, licenses, onboarding, entitlement, release, command, tracker] = await Promise.all([
    apiRequest<{ user?: BusinessUser; customer?: BusinessUser; licenses?: LicensePayload[]; release?: Partial<ReleaseManifest>; onboarding?: OnboardingStatus }>("/v1/me", { token: accessToken }),
    apiRequest<{ licenses: LicensePayload[] }>("/v1/licenses", { token: accessToken }),
    apiRequest<OnboardingStatus>("/v1/onboarding/status", { token: accessToken }),
    connectorToken
      ? apiRequest<Entitlement>("/v1/entitlements/current", { connectorToken })
      : Promise.resolve({ ok: false, status: 0, data: null, error: "Connector token not registered." } as ApiResult<Entitlement>),
    apiRequest<Partial<ReleaseManifest>>("/v1/releases/latest", { token: accessToken }),
    connectorToken
      ? apiRequest<CommandStatus>("/v1/commands/latest", { connectorToken })
      : Promise.resolve({ ok: false, status: 0, data: null, error: "Connector token not registered." } as ApiResult<CommandStatus>),
    getTrackerHealth(errors, connectorToken || accessToken)
  ]);

  for (const [label, result] of [
    ["profile", me],
    ["licenses", licenses],
    ["onboarding", onboarding],
    ["entitlement", entitlement],
    ["release", release],
    ["command", command]
  ] as const) {
    if (!result.ok) {
      errors.push(`${label}: ${result.error || `HTTP ${result.status}`}`);
    }
  }

  return {
    apiOnline: health.status === "online",
    health,
    user: me.data?.customer || me.data?.user || null,
    licenses: licenses.data?.licenses || me.data?.licenses || [],
    entitlement: entitlement.data,
    release: release.data ? normalizeRelease(release.data) : null,
    command: command.data,
    tracker,
    onboarding: onboarding.data || me.data?.onboarding || null,
    errors
  };
}

export async function getAdminCustomers(accessToken?: string | null): Promise<AdminCustomersResult> {
  if (!accessToken) {
    return {
      ok: false,
      status: "unauthorized",
      detail: "Admin login required.",
      customers: []
    };
  }

  const result = await apiRequest<{ customers: BusinessUser[] }>("/v1/admin/customers", {
    token: accessToken
  });

  if (!result.ok || !result.data) {
    const status =
      result.status === 0
        ? "offline"
        : result.status === 401
          ? "unauthorized"
          : result.status === 403
            ? "forbidden"
            : "error";
    return {
      ok: false,
      status,
      detail: result.error || "Admin customers unavailable.",
      customers: []
    };
  }

  return {
    ok: true,
    status: "ok",
    detail: "Admin customer state returned by the service.",
    customers: result.data.customers.map(normalizeAdminCustomer)
  };
}

export function getPrimaryLicense(snapshot: PortalSnapshot) {
  return snapshot.licenses[0] || null;
}

export function isLicenseActive(snapshot: PortalSnapshot) {
  const license = getPrimaryLicense(snapshot);
  return Boolean(
    license?.is_active ||
      activeLicenseStatuses.has(license?.status || "") ||
      activeLicenseStatuses.has(snapshot.entitlement?.status || "") ||
      activeLicenseStatuses.has(snapshot.user?.license_status || "")
  );
}

export function isDisclosureAccepted(snapshot: PortalSnapshot) {
  return Boolean(snapshot.entitlement?.disclosure_accepted ?? snapshot.user?.disclosure_accepted);
}

export function isBrokerBound(snapshot: PortalSnapshot) {
  return Boolean(snapshot.entitlement?.account_bound ?? snapshot.onboarding?.gates?.broker_bound ?? snapshot.user?.broker_account_bound);
}

export function isCommandActive(snapshot: PortalSnapshot) {
  return snapshot.command?.command.execution_authority === true;
}

export function isTrackerAccessible(snapshot: PortalSnapshot) {
  return Boolean(snapshot.tracker?.alive && snapshot.tracker.tracking_enabled);
}

export function buildAccessGates(snapshot: PortalSnapshot, connectorToken?: string | null): AccessGate[] {
  const license = getPrimaryLicense(snapshot);
  const licenseActive = isLicenseActive(snapshot);
  const disclosureAccepted = isDisclosureAccepted(snapshot);
  const brokerBound = isBrokerBound(snapshot);
  const connectorRegistered = Boolean(connectorToken || snapshot.onboarding?.gates?.device_registered);
  const user = snapshot.user;
  const emailGateReported =
    typeof user?.email_verified === "boolean" || typeof snapshot.onboarding?.gates?.email_verified === "boolean";
  const emailPassed = emailGateReported
    ? Boolean(user?.email_verified || snapshot.onboarding?.gates?.email_verified)
    : Boolean(user?.email);
  const trackerReady = Boolean(
    snapshot.tracker?.alive &&
      snapshot.tracker.tracking_enabled &&
      user &&
      emailPassed &&
      licenseActive &&
      disclosureAccepted &&
      brokerBound &&
      connectorRegistered
  );

  return [
    {
      id: "registration",
      label: "Registration",
      value: user ? user.email : "Not signed in",
      detail: user
        ? "Your customer account is recognized for this portal session."
        : "Sign in or create an account before protected services can open.",
      status: user ? "signed in" : "required",
      passed: Boolean(user),
      tone: user ? "good" : "blocked"
    },
    {
      id: "email-confirmation",
      label: "Email confirmation",
      value: emailPassed ? "Confirmed" : "Not confirmed",
      detail: emailGateReported
        ? emailPassed
          ? "Your email is confirmed for account and payment communication."
          : "Confirm your email before checkout, broker connection, downloads, or tracker access."
        : "Your signed-in email is present; final confirmation will be enforced by the account service.",
      status: emailPassed ? "confirmed" : "required",
      passed: emailPassed,
      tone: emailPassed ? "good" : "blocked"
    },
    {
      id: "license",
      label: "Plan and license",
      value: licenseActive ? "Active" : "Not active",
      detail: license
        ? `${license.package_profile?.name || "Package"} profile: ${license.runtime_policy?.runtime_label || "runtime policy recorded"}. Valid through ${formatEpoch(license.expires_at_epoch)}.`
        : "Select a package; paid plans remain staged until payment collection is connected.",
      status: licenseActive ? "active" : "blocked",
      passed: licenseActive,
      tone: licenseActive ? "good" : "blocked"
    },
    {
      id: "disclosure",
      label: "Risk disclosure",
      value: disclosureAccepted ? "Accepted" : "Required",
      detail: disclosureAccepted
        ? "Your risk acknowledgement is recorded for this account."
        : "Read and accept the risk disclosure before live workspace access.",
      status: disclosureAccepted ? "accepted" : "required",
      passed: disclosureAccepted,
      tone: disclosureAccepted ? "good" : "warn"
    },
    {
      id: "broker-binding",
      label: "Broker connection",
      value: brokerBound ? "Connected" : "Not connected",
      detail: brokerBound
        ? "A broker server and account number are linked without storing a broker password."
        : "Add your broker server and account number after accepting the disclosure.",
      status: brokerBound ? "connected" : "required",
      passed: brokerBound,
      tone: brokerBound ? "good" : "warn"
    },
    {
      id: "device-health",
      label: "Device health",
      value: connectorRegistered ? "Device connected" : "Device not connected",
      detail: connectorRegistered
        ? "Your workstation has checked in and can be monitored from the portal."
        : "Register the device that will run the connector before tracker access.",
      status: connectorRegistered ? "connected" : "required",
      passed: connectorRegistered,
      tone: connectorRegistered ? "good" : "warn"
    },
    {
      id: "tracker-launch",
      label: "Tracker health",
      value: trackerReady ? "Ready" : "Waiting",
      detail: snapshot.tracker
        ? "The tracker workspace opens only after account, license, disclosure, broker, and device checks pass."
        : "Tracker readiness will appear after sign-in and setup.",
      status: trackerReady ? "ready" : "locked",
      passed: trackerReady,
      tone: trackerReady ? "good" : "blocked"
    }
  ];
}

export function areAccessGatesPassed(gates: AccessGate[]) {
  return gates.every((gate) => gate.passed);
}

export function firstBlockedGate(gates: AccessGate[]) {
  return gates.find((gate) => !gate.passed) || null;
}

function safeJsonParse<T>(text: string): T | null {
  try {
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

async function getTrackerHealth(errors: string[], token?: string | null): Promise<TrackerHealth | null> {
  const result = await apiRequest<TrackerHealth>("/v1/tracker/status");
  if (result.ok && result.data) {
    return result.data;
  }

  if (token) {
    const access = await apiRequest<TrackerAccessPayload>("/app/tracker", { token });
    if (access.ok && access.data) {
      return {
        alive: access.data.allowed !== false,
        mode: access.data.status || "business-access",
        session_id: access.data.default_session_id,
        tracking_enabled: access.data.allowed !== false
      };
    }
  }

  const mobileHealth = await apiRequest<{ status: string }>("/v1/mobile/health");
  if (!mobileHealth.ok) {
    errors.push(`tracker: ${result.error || mobileHealth.error || `HTTP ${result.status}`}`);
    return null;
  }
  return {
    alive: mobileHealth.data?.status === "ok",
    mode: "mobile-api",
    tracking_enabled: mobileHealth.data?.status === "ok"
  };
}

function normalizeRelease(payload: Partial<ReleaseManifest>): ReleaseManifest {
  const version = payload.version || payload.connector_version || payload.ea_version || "unreported";

  return {
    id: payload.id || `release-${version}`,
    channel: payload.channel || "locked",
    version,
    ea_version: payload.ea_version || version,
    connector_version: payload.connector_version || version,
    minimum_connector_version: payload.minimum_connector_version || version,
    download_url: payload.download_url || "",
    sha256_manifest: payload.sha256_manifest || "Pending release verification",
    status: payload.status || payload.license_status || "locked",
    license_status: payload.license_status
  };
}

function normalizeAdminCustomer(customer: BusinessUser): AdminCustomer {
  return {
    ...customer,
    customer_id: customer.customer_id || customer.id || "",
    license_id: customer.license_id || "",
    full_name: customer.full_name || customer.email.split("@")[0] || customer.customer_id || customer.id || "",
    status: customer.status || customer.license_status || "unknown",
    license_count: customer.license_id ? 1 : 0
  };
}

function formatEpoch(epoch?: number) {
  if (!epoch) {
    return "unreported";
  }

  return new Intl.DateTimeFormat("en", {
    month: "short",
    day: "numeric",
    year: "numeric"
  }).format(new Date(epoch * 1000));
}
