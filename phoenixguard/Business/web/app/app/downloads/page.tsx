import Link from "next/link";
import { CloudDownload, FileCheck2, LockKeyhole } from "lucide-react";
import { releaseRows } from "@/lib/site-data";
import {
  areAccessGatesPassed,
  buildAccessGates,
  getPortalSnapshot,
} from "@/lib/business-api";
import { getServerTokens } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function DownloadsPage() {
  const { accessToken, connectorToken } = await getServerTokens();
  const snapshot = await getPortalSnapshot({ accessToken, connectorToken });
  const gates = buildAccessGates(snapshot, connectorToken);
  const release = snapshot.release;
  const downloadUnlocked = Boolean(
    accessToken &&
      release?.download_url &&
      snapshot.onboarding?.allowed &&
      areAccessGatesPassed(gates)
  );
  const rows = release
    ? [
        {
          version: release.version,
          channel: release.channel,
          artifact: release.download_url ? "PhoenixGuard connector release" : "Release is not available yet",
          hash: release.sha256_manifest,
          state: downloadUnlocked ? release.status : "Locked"
        },
        ...releaseRows.slice(1)
      ]
    : releaseRows;

  return (
    <main className="portal-main">
      <section className="portal-section">
        <span className="eyebrow">Release Download</span>
        <h2>{downloadUnlocked ? "Your connector release is ready." : "Release access is locked."}</h2>
        <p>
          {downloadUnlocked
            ? "Use the secure download link for the current connector package."
            : "Downloads require a signed-in customer, active license, accepted disclosure, and completed setup."}
        </p>
      </section>

      <section className="matrix-wrap portal-stack">
        <table className="release-table" data-testid="release-download-table">
          <thead>
            <tr>
              <th>Version</th>
              <th>Channel</th>
              <th>Artifact</th>
              <th>Verification</th>
              <th>State</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={`${row.version}-${row.channel}`}>
                <th scope="row">{row.version}</th>
                <td>{row.channel}</td>
                <td>
                  <CloudDownload aria-hidden="true" size={16} /> {row.artifact}
                </td>
                <td>{row.hash}</td>
                <td>{row.state}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="portal-section portal-stack">
        <span className="eyebrow">Disclosure Pin</span>
          <h2>Risk acknowledgement stays paired with release access.</h2>
        <p>
          <FileCheck2 aria-hidden="true" size={17} /> Setup state:{" "}
          {gates.map((gate) => `${gate.label}=${gate.status}`).join(" / ")}
        </p>
        <div className="page-actions">
          {downloadUnlocked && release?.download_url ? (
            <Link className="command-link command-link--solid" href={release.download_url} target="_blank">
              <CloudDownload aria-hidden="true" size={16} />
              <span>Open Secure Download</span>
            </Link>
          ) : (
            <button className="command-link command-link--solid" disabled type="button">
              <LockKeyhole aria-hidden="true" size={16} />
              <span>Download Locked</span>
            </button>
          )}
          <Link className="command-link command-link--ghost" href="/risk-disclosure">
            <span>Open Disclosure</span>
          </Link>
        </div>
      </section>
    </main>
  );
}
