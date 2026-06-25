import Link from "next/link";
import { ExternalLink, LockKeyhole, RadioTower, ShieldCheck } from "lucide-react";
import {
  areAccessGatesPassed,
  buildAccessGates,
  firstBlockedGate,
  getPortalSnapshot,
  isTrackerAccessible
} from "@/lib/business-api";
import { getServerTokens } from "@/lib/server-session";
import { getTrackerDashboardUrl } from "@/lib/tracker";

export const dynamic = "force-dynamic";

export default async function TrackerPage() {
  const { accessToken, connectorToken } = await getServerTokens();
  const snapshot = await getPortalSnapshot({ accessToken, connectorToken });
  const gates = buildAccessGates(snapshot, connectorToken);
  const blockedGate = firstBlockedGate(gates);
  const servicesReady = areAccessGatesPassed(gates) && isTrackerAccessible(snapshot);
  const trackerUrl = getTrackerDashboardUrl();
  const statusText = servicesReady && snapshot.tracker?.alive ? "Tracker ready" : "Tracker locked";

  return (
    <main className="portal-main">
      <section className="portal-section tracker-frame-wrap" data-testid="tracker-gui">
        <span className="eyebrow">Tracker Workspace</span>
        <h2 data-testid="tracker-status">{statusText}</h2>
        <p>
          {servicesReady
            ? "The protected tracker workspace is available for this account."
            : `Complete ${blockedGate ? blockedGate.label.toLowerCase() : "the setup checklist"} before tracker access opens.`}
        </p>

        <div className="tracker-launchbar" data-testid="tracker-launch-state">
          <div>
            <span className="eyebrow">Readiness</span>
            <code data-testid="tracker-entitlement">
              {servicesReady ? "ready" : blockedGate?.status || "locked"}
            </code>
          </div>
          <div>
            <span className="eyebrow">Workspace</span>
            <code data-testid="tracker-url">{servicesReady ? "available" : "protected until setup is complete"}</code>
          </div>
          {servicesReady ? (
            <Link className="icon-command" href={trackerUrl} target="_blank" aria-label="Open tracker dashboard">
              <ExternalLink aria-hidden="true" size={18} />
            </Link>
          ) : (
            <Link className="icon-command" href="/app" aria-label="Return to onboarding">
              <LockKeyhole aria-hidden="true" size={18} />
            </Link>
          )}
        </div>

        {servicesReady ? (
          <iframe
            className="tracker-frame"
            data-testid="tracker-frame"
            src={trackerUrl}
            title="PhoenixGuard tracker dashboard"
          />
        ) : (
          <div className="service-locked-panel" data-testid="tracker-frame-locked">
            <LockKeyhole aria-hidden="true" size={24} />
            <strong>Tracker workspace locked</strong>
            <span>Complete registration, payment, disclosure, broker connection, and device setup first.</span>
          </div>
        )}
      </section>

      <section className="portal-section portal-stack">
        <span className="eyebrow">Connection Context</span>
        <h2>Device and plan readiness stay visible.</h2>
        <div className="metric-stack">
          <div className="metric-row">
            <span>Account</span>
            <strong>{snapshot.user?.email || "not signed in"}</strong>
          </div>
          <div className="metric-row">
            <span>Plan</span>
            <strong>{snapshot.licenses[0]?.status || snapshot.user?.license_status || "not active"}</strong>
          </div>
          <div className="metric-row">
            <span>Tracker health</span>
            <strong>
              <RadioTower aria-hidden="true" size={16} />{" "}
              {snapshot.tracker?.alive ? "alive" : "unavailable"}
            </strong>
          </div>
          <div className="metric-row">
            <span>Protection</span>
            <strong>
              <ShieldCheck aria-hidden="true" size={16} /> {servicesReady ? "Passed" : "Locked"}
            </strong>
          </div>
          <div className="metric-row">
            <span>License</span>
            <strong data-testid="license-status">
              {snapshot.licenses[0]?.status || snapshot.user?.license_status || "not signed in"}
            </strong>
          </div>
        </div>
      </section>
    </main>
  );
}
