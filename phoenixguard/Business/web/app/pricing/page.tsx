import Link from "next/link";
import { ArrowRight, CheckCircle2 } from "lucide-react";
import { AmbientBackground } from "@/components/AmbientBackground";
import { PublicNav } from "@/components/PublicNav";
import { planCards, pricingRows } from "@/lib/site-data";

export default function PricingPage() {
  return (
    <main className="page-shell">
      <AmbientBackground />
      <PublicNav />

      <section className="page-hero">
        <span className="hero__product">808Fx Standard Hybrid System</span>
        <h1>Choose the operating window that fits your market routine.</h1>
        <p>
          Begin with a free preview, move into focused daily access, or choose continuous
          professional availability. Every package keeps risk acknowledgement, account
          verification, and workspace readiness in view.
        </p>
        <div className="page-actions">
          <Link className="command-link command-link--solid" href="/login">
            <span>Create Account</span>
            <ArrowRight aria-hidden="true" size={16} />
          </Link>
          <Link className="command-link command-link--ghost" href="/risk-disclosure">
            <span>Read Disclosure</span>
          </Link>
        </div>
      </section>

      <section className="matrix-wrap plan-grid plan-grid--pricing" aria-label="Monthly access plans">
        {planCards.map((plan) => {
          const Icon = plan.icon;
          return (
            <article className="plan-card plan-card--pricing" key={plan.code}>
              <div className="plan-card__head">
                <Icon aria-hidden="true" size={24} />
                <div>
                  <span>{plan.runtime}</span>
                  <h2>{plan.name}</h2>
                </div>
              </div>
              <div className="plan-card__price">
                <strong>{plan.price}</strong>
                <span>{plan.cadence}</span>
              </div>
              <p>{plan.bestFor}</p>
              <ul>
                {plan.includes.map((item) => (
                  <li key={item}>
                    <CheckCircle2 aria-hidden="true" size={16} />
                    <span>{item}</span>
                  </li>
                ))}
              </ul>
              <Link className="command-link command-link--solid" href="/login">
                <span>
                  {plan.code === "scale-review"
                    ? "Start Review"
                    : plan.code === "hybrid-free-2h"
                      ? "Start Free Preview"
                      : "Create Account"}
                </span>
                <ArrowRight aria-hidden="true" size={16} />
              </Link>
            </article>
          );
        })}
      </section>

      <section className="matrix-wrap" aria-label="Pricing comparison">
        <table className="pricing-matrix">
          <thead>
            <tr>
              <th>Capability</th>
              <th>Free Preview</th>
              <th>Standard Access</th>
              <th>Professional Access</th>
              <th>Scale Review</th>
            </tr>
          </thead>
          <tbody>
            {pricingRows.map((row) => (
              <tr key={row.item}>
                <th scope="row">{row.item}</th>
                <td>{row.free}</td>
                <td>{row.standard}</td>
                <td>{row.professional}</td>
                <td>{row.scale}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
