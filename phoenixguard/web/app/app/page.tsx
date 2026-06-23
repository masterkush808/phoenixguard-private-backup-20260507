import Link from "next/link";
import {
  ArrowRight,
  CheckCircle2,
  CloudDownload,
  Clock3,
  LockKeyhole,
  RadioTower
} from "lucide-react";
import { OnboardingConsole } from "@/components/OnboardingConsole";
import {
  areAccessGatesPassed,
  buildAccessGates,
  firstBlockedGate,
  getPortalSnapshot,
  getPrimaryLicense,
  isTrackerAccessible
} from "@/lib/business-api";
import { getServerTokens } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function PortalDashboardPage() {
  const { accessToken, connectorToken } = await getServerTokens();
  const snapshot = await getPortalSnapshot({ accessToken, connectorToken });
  const gates = buildAccessGates(snapshot, connectorToken);
  const blockedGate = firstBlockedGate(gates);
  const servicesReady = areAccessGatesPassed(gates) && isTrackerAccessible(snapshot);
  const license = getPrimaryLicense(snapshot);
  const completedGateCount = gates.filter((gate) => gate.passed).length;
  const progress = Math.round((completedGateCount / gates.length) * 100);
  const accountLabel = snapshot.user?.email || "Not signed in";
  const packageLabel = license?.package_profile?.name || license?.plan_code || "No package selected";
  const runtimeLabel = license?.runtime_policy?.runtime_label || "No runtime window assigned";
  const trackerLabel = servicesReady ? "Ready" : "Locked";
  const currentBlocker = blockedGate?.label || "All requirements complete";
  const currentDetail = servicesReady
    ? "Your account is eligible to open the protected tracker."
    : blockedGate?.detail || "Refresh the account state after completing the setup controls below.";

  const summaryRows = [
    { label: "Account", value: accountLabel },
    { label: "Package", value: packageLabel },
    { label: "Runtime", value: runtimeLabel },
    { label: "Tracker", value: trackerLabel },
    { label: "Business API", value: snapshot.health.status === "online" ? "Online" : "Offline" }
  ];

  return (
    <main className="portal-main portal-main--activation">
      <section className="activation-brief" aria-labelledby="activation-heading">
        <div className="activation-brief__copy">
          <span className="eyebrow">Client Activation</span>
          <h2 id="activation-heading">
            {servicesReady ? "Tracker access is active." : "Complete these checks to open tracker access."}
          </h2>
          <p>
            {servicesReady
              ? "Registration, package certification, disclosure, broker connection, device health, and tracker readiness are confirmed for this account."
              : `Current blocker: ${currentBlocker}. Use the activation controls below to finish the next required step.`}
          </p>
        </div>

        <div className="activation-progress" aria-label="Activation progress">
          <strong>{completedGateCount}/{gates.length}</strong>
          <span>requirements complete</span>
          <div className="activation-progress__track" aria-hidden="true">
            <span style={{ width: `${progress}%` }} />
          </div>
        </div>
      </section>

      <section className="activation-workspace" aria-label="Activation workspace">
        <div className="activation-path">
          <div className="activation-section-heading">
            <span className="eyebrow">Activation Path</span>
            <h3>Finish the setup in order.</h3>
            <p>Each row unlocks the next one. Tracker access stays closed until every requirement is confirmed.</p>
          </div>

          <ol className="activation-step-list">
            {gates.map((gate, index) => (
              <li className={`activation-step activation-step--${gate.tone}`} data-testid={gate.id} key={gate.id}>
                <span className="activation-step__marker" aria-hidden="true">
                  {gate.passed ? <CheckCircle2 size={17} /> : index + 1}
                </span>
                <span className="activation-step__body">
                  <strong>{gate.label}</strong>
                  <small>{gate.detail}</small>
                </span>
                <em>{gate.status}</em>
              </li>
            ))}
          </ol>
        </div>

        <aside className="activation-current" aria-label="Current activation status">
          <span className="eyebrow">Current Status</span>
          <h3>{servicesReady ? "Ready to launch." : currentBlocker}</h3>
          <p>{currentDetail}</p>

          <div className="activation-summary">
            {summaryRows.map((item) => (
              <div className="activation-summary__row" key={item.label}>
                <span>{item.label}</span>
                <strong>{item.value}</strong>
              </div>
            ))}
          </div>

          <div className="activation-actions">
            {servicesReady ? (
              <Link className="command-link command-link--solid" href="/app/tracker">
                <RadioTower aria-hidden="true" size={16} />
                <span>Launch Tracker</span>
                <ArrowRight aria-hidden="true" size={16} />
              </Link>
            ) : (
              <Link className="command-link command-link--ghost" href="#activation-controls">
                <Clock3 aria-hidden="true" size={16} />
                <span>Continue Setup</span>
              </Link>
            )}
            <Link className="icon-command" href="/app/downloads" aria-label="Open downloads">
              <CloudDownload aria-hidden="true" size={18} />
            </Link>
          </div>

          {!servicesReady ? (
            <p className="activation-current__note">
              <LockKeyhole aria-hidden="true" size={15} />
              Protected services remain unavailable until the checklist reaches 100%.
            </p>
          ) : null}
        </aside>
      </section>

      <div className="portal-stack" id="activation-controls">
        <OnboardingConsole initialSnapshot={snapshot} />
      </div>
    </main>
  );
}
