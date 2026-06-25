"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  ArrowRight,
  Cable,
  CircleAlert,
  CreditCard,
  FileCheck2,
  HeartPulse,
  RadioTower,
  ServerCog
} from "lucide-react";
import {
  DISCLOSURE_VERSION,
  PortalSnapshot,
  areAccessGatesPassed,
  buildAccessGates,
  firstBlockedGate,
  getPrimaryLicense,
  isTrackerAccessible
} from "@/lib/business-api";
import { csrfHeaders } from "@/lib/client-session";

type OnboardingConsoleProps = {
  initialSnapshot: PortalSnapshot;
};

type BusyState = "checkout" | "disclosure" | "broker" | "device" | "heartbeat" | "refresh" | null;
type PlanChoice = "hybrid-free-2h" | "hybrid-standard-6h" | "hybrid-professional-24x7" | "scale-review";

const planLabels: Record<PlanChoice, { price: string; runtime: string; action: string }> = {
  "hybrid-free-2h": { price: "$0", runtime: "2 hours daily", action: "Activate Preview" },
  "hybrid-standard-6h": { price: "$20", runtime: "6 hours daily", action: "Stage Standard" },
  "hybrid-professional-24x7": { price: "$100", runtime: "24/7 eligible", action: "Stage Professional" },
  "scale-review": { price: "Review", runtime: "Custom controls", action: "Stage Review" }
};

export function OnboardingConsole({ initialSnapshot }: OnboardingConsoleProps) {
  const [snapshot, setSnapshot] = useState(initialSnapshot);
  const [busy, setBusy] = useState<BusyState>(null);
  const [message, setMessage] = useState(
    "Choose the correct access package, accept the disclosure, connect broker details, and register the device. Paid packages stay staged while payments are paused."
  );
  const [brokerServer, setBrokerServer] = useState("");
  const [accountNumber, setAccountNumber] = useState("");
  const [selectedPlan, setSelectedPlan] = useState<PlanChoice>("hybrid-standard-6h");
  const [deviceFingerprint, setDeviceFingerprint] = useState("primary-workstation");
  const [deviceLabel, setDeviceLabel] = useState("Primary workstation");
  const [licenseKey, setLicenseKey] = useState(initialSnapshot.licenses[0]?.license_key || "");
  const gates = useMemo(() => buildAccessGates(snapshot, null), [snapshot]);
  const blockedGate = firstBlockedGate(gates);
  const serviceReady = areAccessGatesPassed(gates) && isTrackerAccessible(snapshot);
  const license = getPrimaryLicense(snapshot);
  const licenseStatus = license?.status || snapshot.user?.license_status || "not signed in";
  const emailConfirmed = gates.find((gate) => gate.id === "email-confirmation")?.passed ?? false;
  const licenseReady = gates.find((gate) => gate.id === "license")?.passed ?? false;
  const disclosureReady = gates.find((gate) => gate.id === "disclosure")?.passed ?? false;
  const brokerReady = gates.find((gate) => gate.id === "broker-binding")?.passed ?? false;
  const deviceReady = gates.find((gate) => gate.id === "device-health")?.passed ?? false;
  const signedIn = Boolean(snapshot.user);
  const certification = license?.package_certification || snapshot.onboarding?.package_certification;
  const runtimeState = license?.runtime_state || snapshot.onboarding?.runtime_state || null;
  const runtimePolicy = license?.runtime_policy || snapshot.entitlement?.runtime_policy || null;
  const packageName = certification?.package_name || license?.package_profile?.name || "No package certified";
  const packageStatus = certification?.status ? certification.status.replaceAll("_", " ") : "waiting";
  const remainingHours =
    runtimeState?.remaining_seconds === undefined
      ? "not started"
      : `${Math.max(0, runtimeState.remaining_seconds / 3600).toFixed(1)}h left today`;

  useEffect(() => {
    if (initialSnapshot.user) {
      void refresh("refresh");
    }
  }, []);

  useEffect(() => {
    const nextKey = snapshot.licenses[0]?.license_key;
    if (nextKey && !licenseKey) {
      setLicenseKey(nextKey);
    }
  }, [licenseKey, snapshot.licenses]);

  async function refresh(reason: BusyState = "refresh") {
    if (!snapshot.user && reason !== "refresh") {
      setMessage("Sign in before refreshing your setup.");
      return;
    }
    setBusy(reason);
    const response = await fetch("/api/portal/snapshot", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store"
    });
    if (response.ok) {
      const nextSnapshot = (await response.json()) as PortalSnapshot;
      setSnapshot(nextSnapshot);
    } else {
      setMessage("Setup state could not be refreshed yet.");
    }
    setBusy(null);
  }

  async function acceptDisclosure(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!snapshot.user) {
      setMessage("Sign in before accepting the risk disclosure.");
      return;
    }
    if (!emailConfirmed) {
      setMessage("Confirm your email before accepting the risk disclosure.");
      return;
    }
    setBusy("disclosure");
    const response = await fetch("/api/onboarding/disclosure", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify({ accepted: true, version: DISCLOSURE_VERSION })
    });
    if (!response.ok) {
      setBusy(null);
      setMessage("The disclosure could not be recorded yet. Check your account status and try again.");
      return;
    }
    setMessage("Risk disclosure accepted.");
    await refresh("disclosure");
  }

  async function startCheckout() {
    if (!snapshot.user?.email) {
      setMessage("Checkout needs a signed-in and confirmed account.");
      return;
    }
    if (!emailConfirmed) {
      setMessage("Confirm your email before checkout can open.");
      return;
    }
    setBusy("checkout");
    const response = await fetch("/api/onboarding/checkout", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify({
        customer_email: snapshot.user.email,
        customer_id: snapshot.user.customer_id || snapshot.user.id,
        plan_code: selectedPlan
      })
    });
    const payload = (await response.json().catch(() => null)) as {
      checkout_url?: string;
      checkout_session_id?: string;
      license?: { status?: string; plan_code?: string; license_key?: string };
      provider?: string;
      status?: string;
      message?: string;
      detail?: string;
    } | null;
    if (!response.ok) {
      setBusy(null);
      setMessage(response.status === 403 ? "Confirm your email before checkout can open." : payload?.detail || "Live payment processing is not connected yet.");
      return;
    }
    if (payload?.provider === "free-preview") {
      if (payload.license?.license_key) {
        setLicenseKey(payload.license.license_key);
      }
      setMessage(payload.message || "Free preview activated. Continue through disclosure, broker, and device setup before the workspace opens.");
    } else if (payload?.provider === "payment-paused") {
      setMessage(payload.message || "Package staged. Payment collection is paused, so no paid license is active yet.");
    } else if (payload?.provider === "manual-review") {
      setMessage(payload.message || "Review staged. Activation remains locked until the package is approved.");
    } else if (payload?.checkout_url) {
      setMessage("Secure checkout opened in a new tab.");
      window.open(payload.checkout_url, "_blank", "noopener,noreferrer");
    } else {
      setMessage("Checkout started, but the payment page was not returned yet.");
    }
    await refresh("checkout");
  }

  async function bindBroker(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!snapshot.user) {
      setMessage("Sign in before connecting a broker account.");
      return;
    }
    if (!emailConfirmed || !licenseReady || !disclosureReady) {
      setMessage("Confirm email, activate an eligible package license, and accept the risk disclosure before connecting a broker account.");
      return;
    }
    setBusy("broker");
    const response = await fetch("/api/onboarding/broker", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify({
        broker_server: brokerServer,
        mt4_account_number: accountNumber,
        label: "808Fx portal onboarding"
      })
    });
    if (!response.ok) {
      setBusy(null);
      setMessage("Broker connection could not be saved. Confirm the account details and try again.");
      return;
    }
    setMessage("Broker account connected.");
    await refresh("broker");
  }

  async function registerDevice(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!licenseKey) {
      setMessage("Enter your license key before connecting this device.");
      return;
    }
    if (!emailConfirmed || !licenseReady || !disclosureReady || !brokerReady) {
      setMessage("Complete email confirmation, package activation, disclosure acceptance, and broker connection before device setup.");
      return;
    }
    setBusy("device");
    const response = await fetch("/api/onboarding/device", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify({
        license_key: licenseKey,
        device_fingerprint: deviceFingerprint,
        device_label: deviceLabel,
        connector_version: "808fx-standard-hybrid-2026.06"
      })
    });
    const payload = (await response.json().catch(() => null)) as {
      connector_token: string;
      device_id: string;
    } | null;
    if (!response.ok || !payload?.connector_token) {
      setBusy(null);
      setMessage("Device connection could not be completed. Confirm the license key and try again.");
      return;
    }
    setMessage("Device connected. Sending heartbeat now.");
    await sendHeartbeat("device");
    await refresh("device");
  }

  async function sendHeartbeat(reason: BusyState = "heartbeat") {
    setBusy(reason);
    const response = await fetch("/api/onboarding/heartbeat", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify({})
    });
    if (!response.ok) {
      setBusy(null);
      setMessage("Heartbeat could not be recorded yet. Check the device connection and try again.");
      return;
    }
    setMessage("Device heartbeat received.");
    setBusy(null);
  }

  return (
    <section className="portal-section onboarding-console onboarding-console--activation" aria-label="Customer setup checklist">
      <div className="onboarding-console__head">
        <div>
          <span className="eyebrow">Activation Controls</span>
          <h2>Complete setup in order.</h2>
          <p>
            Plan status: <strong data-testid="license-status">{licenseStatus}</strong>.{" "}
            {serviceReady
              ? "Tracker access is available for this account."
              : `Next required step${blockedGate ? `: ${blockedGate.label}` : " will appear here"}.`}
          </p>
        </div>
        <button
          className="icon-command"
          type="button"
          aria-label="Refresh setup state"
          disabled={busy !== null}
          onClick={() => void refresh()}
        >
          <HeartPulse aria-hidden="true" size={18} />
        </button>
      </div>

      <div className="form-status" role="status">
        <CircleAlert aria-hidden="true" size={16} />
        <span>{busy ? "Saving and refreshing account state..." : message}</span>
      </div>

      <div className="onboarding-block__head">
        <div>
          <span className="eyebrow">Package State</span>
          <h3>Certified access profile</h3>
        </div>
        <p>Runtime and freshness rules are tied to the selected package before tracker access can open.</p>
      </div>

      <div className="package-certification" aria-label="Certified package profile">
        <div>
          <span>Package</span>
          <strong>{packageName}</strong>
          <em>{packageStatus}</em>
        </div>
        <div>
          <span>Runtime</span>
          <strong>{runtimePolicy?.runtime_label || planLabels[selectedPlan].runtime}</strong>
          <em>{remainingHours}</em>
        </div>
        <div>
          <span>Freshness</span>
          <strong>
            {runtimePolicy?.heartbeat_freshness_seconds
              ? `${runtimePolicy.heartbeat_freshness_seconds}s heartbeat`
              : "activated after package"}
          </strong>
          <em>{runtimePolicy?.tier || planLabels[selectedPlan].price}</em>
        </div>
      </div>

      <div className="onboarding-block__head">
        <div>
          <span className="eyebrow">Checklist</span>
          <h3>Access gates</h3>
        </div>
        <p>Every gate must read complete before protected tracker services are available.</p>
      </div>

      <div className="gate-ledger" aria-label="Current setup status">
        {gates.map((gate) => (
          <div
            className={`gate-ledger__row gate-ledger__row--${gate.tone}`}
            key={gate.id}
            data-testid={`gate-ledger-${gate.id}`}
          >
            <span>{gate.label}</span>
            <strong>{gate.value}</strong>
            <em>{gate.status}</em>
          </div>
        ))}
      </div>

      <form className="onboarding-flow__row onboarding-flow__row--disclosure" onSubmit={acceptDisclosure}>
        <label className="onboarding-flow__check">
          <input
            data-testid="disclosure-accept-checkbox"
            disabled={!signedIn || !emailConfirmed || busy !== null}
            required
            type="checkbox"
          />
          <span>I have read and accept the risk disclosure.</span>
        </label>
        <button
          className="command-link command-link--ghost"
          data-testid="disclosure-accept-submit"
          disabled={!signedIn || !emailConfirmed || busy !== null}
          type="submit"
        >
          <FileCheck2 aria-hidden="true" size={16} />
          <span>{snapshot.entitlement?.disclosure_accepted ? "Disclosure Accepted" : "Record Acceptance"}</span>
        </button>
      </form>

      <div className="onboarding-flow__row onboarding-flow__row--actions">
        <label>
          <span>Account email</span>
          <input readOnly value={snapshot.user?.email || "Sign in before checkout"} />
        </label>
        <div className="plan-toggle" aria-label="Access plan">
          {(Object.keys(planLabels) as PlanChoice[]).map((planCode) => (
            <button
              className={selectedPlan === planCode ? "is-active" : ""}
              disabled={!signedIn || !emailConfirmed || busy !== null}
              key={planCode}
              onClick={() => setSelectedPlan(planCode)}
              type="button"
            >
              <strong>{planLabels[planCode].price}</strong>
              <span>{planLabels[planCode].runtime}</span>
            </button>
          ))}
        </div>
        <button
          className="command-link command-link--ghost"
          data-testid="checkout-start-submit"
          disabled={!signedIn || !emailConfirmed || busy !== null}
          onClick={() => void startCheckout()}
          type="button"
        >
          <CreditCard aria-hidden="true" size={16} />
          <span>{planLabels[selectedPlan].action}</span>
        </button>
      </div>

      <form className="onboarding-flow__row" data-testid="broker-binding-form" onSubmit={bindBroker}>
        <label>
          <span>Broker server</span>
          <input
            data-testid="broker-server"
            disabled={!signedIn || !emailConfirmed || !licenseReady || !disclosureReady || busy !== null}
            onChange={(event) => setBrokerServer(event.target.value)}
            placeholder="Server name from your broker"
            required
            value={brokerServer}
          />
        </label>
        <label>
          <span>Trading account number</span>
          <input
            data-testid="mt4-account-number"
            disabled={!signedIn || !emailConfirmed || !licenseReady || !disclosureReady || busy !== null}
            onChange={(event) => setAccountNumber(event.target.value)}
            placeholder="Account number only"
            required
            value={accountNumber}
          />
        </label>
        <button
          className="command-link command-link--ghost"
          data-testid="broker-bind-submit"
          disabled={!signedIn || !emailConfirmed || !licenseReady || !disclosureReady || busy !== null}
          type="submit"
        >
          <ServerCog aria-hidden="true" size={16} />
          <span>{snapshot.entitlement?.account_bound ? "Broker Connected" : "Save Broker Connection"}</span>
        </button>
      </form>

      <form className="onboarding-flow__row" data-testid="device-register-form" onSubmit={registerDevice}>
        <label>
          <span>License key</span>
          <input
            data-testid="license-key"
            disabled={busy !== null || !emailConfirmed || !licenseReady || !disclosureReady || !brokerReady}
            onChange={(event) => setLicenseKey(event.target.value)}
            placeholder="PG-LIVE-LICENSE-KEY"
            value={licenseKey}
          />
        </label>
        <label>
          <span>Device fingerprint</span>
          <input
            data-testid="device-fingerprint"
            disabled={busy !== null || !emailConfirmed || !licenseReady || !disclosureReady || !brokerReady}
            onChange={(event) => setDeviceFingerprint(event.target.value)}
            required
            value={deviceFingerprint}
          />
        </label>
        <button
          className="command-link command-link--ghost"
          data-testid="device-register-submit"
          disabled={busy !== null || !licenseKey || !emailConfirmed || !licenseReady || !disclosureReady || !brokerReady}
          type="submit"
        >
          <Cable aria-hidden="true" size={16} />
          <span>{deviceReady ? "Device Connected" : "Register Device"}</span>
        </button>
      </form>

      <div className="onboarding-flow__row onboarding-flow__row--actions">
        <label>
          <span>Device label</span>
          <input
            data-testid="device-label"
            disabled={busy !== null || !emailConfirmed || !licenseReady || !disclosureReady || !brokerReady}
            onChange={(event) => setDeviceLabel(event.target.value)}
            value={deviceLabel}
          />
        </label>
        <button
          className="command-link command-link--ghost"
          data-testid="device-heartbeat-submit"
          disabled={!deviceReady || busy !== null}
          onClick={() => void sendHeartbeat()}
          type="button"
        >
          <HeartPulse aria-hidden="true" size={16} />
          <span>Refresh Device Health</span>
        </button>
        {serviceReady ? (
          <Link className="command-link command-link--solid" data-testid="open-tracker-gui" href="/app/tracker">
            <RadioTower aria-hidden="true" size={16} />
          <span>Open Tracker</span>
            <ArrowRight aria-hidden="true" size={16} />
          </Link>
        ) : (
          <button className="command-link command-link--solid" data-testid="open-tracker-gui" disabled type="button">
            <RadioTower aria-hidden="true" size={16} />
            <span>Tracker Locked</span>
          </button>
        )}
      </div>
    </section>
  );
}
