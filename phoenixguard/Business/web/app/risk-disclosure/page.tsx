import Link from "next/link";
import { AlertTriangle, ArrowRight, FileCheck2 } from "lucide-react";
import { AmbientBackground } from "@/components/AmbientBackground";
import { PublicNav } from "@/components/PublicNav";
import { disclosurePoints } from "@/lib/site-data";

export default function RiskDisclosurePage() {
  return (
    <main className="page-shell">
      <AmbientBackground />
      <PublicNav />

      <section className="page-hero">
        <span className="hero__product">Risk Disclosure</span>
        <h1>Full Risk Disclosure for the 808Fx Standard Hybrid System.</h1>
        <p>
          Read this carefully before creating an account, activating a free preview, paying for
          access, connecting any broker account, downloading a release, or opening the tracker.
          The PhoenixGuard Engine supports market-tracking operations; it is not a promise of
          trading performance.
        </p>
        <div className="page-actions">
          <Link className="command-link command-link--solid" href="/app">
            <span>Continue To Portal</span>
            <ArrowRight aria-hidden="true" size={16} />
          </Link>
        </div>
      </section>

      <section className="matrix-wrap disclosure-callout">
        <AlertTriangle aria-hidden="true" size={22} />
        <p>
          Access is granted only after this risk notice is acknowledged. Use the system only
          where your broker, country, account type, and instruments permit automated or assisted
          workflows, and seek qualified advice for your own legal, tax, financial, and regulatory
          position.
        </p>
      </section>

      <section className="risk-ledger">
        {disclosurePoints.map((point) => (
          <article className="risk-row" key={point.title}>
            <h2>{point.title}</h2>
            <p>{point.copy}</p>
          </article>
        ))}
        <article className="risk-row">
          <h2>Operator acknowledgement</h2>
          <p>
            <FileCheck2 aria-hidden="true" size={18} /> You must acknowledge this disclosure in
            the portal before protected services, broker connection, downloads, or tracker access
            can open.
          </p>
        </article>
      </section>
    </main>
  );
}
