"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { AlertTriangle, CheckCircle2, ShieldAlert } from "lucide-react";
import { disclosurePoints } from "@/lib/site-data";

const CONSENT_KEY = "pg_808fx_risk_consent_risk-disclosure-2026-06";

export function RiskDisclosureModal() {
  const [visible, setVisible] = useState(false);
  const [confirmed, setConfirmed] = useState(false);

  useEffect(() => {
    try {
      setVisible(window.localStorage.getItem(CONSENT_KEY) !== "accepted");
    } catch {
      setVisible(true);
    }
  }, []);

  function acceptDisclosure() {
    if (!confirmed) {
      return;
    }
    try {
      window.localStorage.setItem(CONSENT_KEY, "accepted");
    } catch {
      // A blocked storage write should not prevent the user from closing after explicit consent.
    }
    setVisible(false);
  }

  if (!visible) {
    return null;
  }

  return (
    <div className="risk-consent-backdrop" role="presentation">
      <section
        aria-labelledby="risk-consent-title"
        aria-modal="true"
        className="risk-consent"
        role="dialog"
      >
        <div className="risk-consent__hero">
          <span className="risk-consent__mark">
            <ShieldAlert aria-hidden="true" size={22} />
          </span>
          <div>
            <span className="eyebrow">Mandatory Risk Disclosure</span>
            <h2 id="risk-consent-title">Read this before entering the 808Fx Standard Hybrid System.</h2>
            <p>
              Forex, CFDs, leveraged products, AI-assisted analytics, and automated or
              semi-automated workflows carry serious risk. You enter, connect accounts, and
              operate the system at your own risk.
            </p>
          </div>
        </div>

        <div className="risk-consent__body">
          {disclosurePoints.map((point) => (
            <article className="risk-consent__point" key={point.title}>
              <h3>{point.title}</h3>
              <p>{point.copy}</p>
            </article>
          ))}
          <article className="risk-consent__notice">
            <AlertTriangle aria-hidden="true" size={18} />
            <p>
              Use this system only with brokers, countries, account types, and products where
              automated or assisted trading is lawful and permitted. This notice is not legal,
              tax, financial, or regulatory advice; consult qualified professionals before using
              the system with any real account or funds.
            </p>
          </article>
        </div>

        <label className="risk-consent__check">
          <input
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
            type="checkbox"
          />
          <span>
            I have read the full risk disclosure, understand that leveraged markets can cause
            significant loss, confirm that I am responsible for broker and jurisdiction
            compliance, and agree to continue at my own risk.
          </span>
        </label>

        <div className="risk-consent__actions">
          <Link className="command-link command-link--ghost" href="/risk-disclosure">
            Read Disclosure Page
          </Link>
          <button
            className="command-link command-link--solid"
            disabled={!confirmed}
            onClick={acceptDisclosure}
            type="button"
          >
            <CheckCircle2 aria-hidden="true" size={16} />
            <span>I Understand And Agree</span>
          </button>
        </div>
      </section>
    </div>
  );
}
