import Link from "next/link";
import { ArrowRight, CheckCircle2, ShieldCheck, Sparkles } from "lucide-react";
import { AmbientBackground } from "@/components/AmbientBackground";
import { PublicNav } from "@/components/PublicNav";
import { backgroundImages, heroSlides, hybridGates, planCards } from "@/lib/site-data";

export default function HomePage() {
  return (
    <main className="public-page">
      <AmbientBackground />
      <PublicNav />

      <section className="hero">
        <div className="hero__content">
          <span className="hero__product">Powered by the PhoenixGuard Engine</span>
          <h1>808Fx Standard Hybrid System</h1>
          <p>
            A premium financial-market tracking SaaS for verified clients who want refined
            analytics, disciplined operating windows, and AI-assisted market awareness in one
            polished workspace.
          </p>
          <p className="hero__promise">
            In-house intelligence, protected onboarding, transparent risk acknowledgement, and
            elegant access control before any live connection is considered.
          </p>
          <div className="hero__actions">
            <Link className="command-link command-link--solid" href="/login">
              <span>Create Account</span>
              <ArrowRight aria-hidden="true" size={16} />
            </Link>
            <Link className="command-link command-link--ghost" href="/risk-disclosure">
              <ShieldCheck aria-hidden="true" size={16} />
              <span>Read Full Risk Disclosure</span>
            </Link>
          </div>

          <div className="command-strip" aria-label="System readiness summary">
            <div>
              <span>Free preview</span>
              <strong>2 hours daily access</strong>
            </div>
            <div>
              <span>Monthly plans</span>
              <strong>$20 focused / $100 24/7</strong>
            </div>
            <div>
              <span>Client protection</span>
              <strong>Verified setup</strong>
            </div>
          </div>
        </div>

        <aside className="hero-intel" aria-label="808Fx system story">
          <div className="hero-intel__backdrop" aria-hidden="true">
            {backgroundImages.map((image, index) => (
              <span
                key={image}
                style={{
                  backgroundImage: `url(${image})`,
                  animationDelay: `${index * 16000 - 3000}ms`
                }}
              />
            ))}
          </div>
          <div className="hero-intel__chrome">
            <span>808Fx Intelligence Brief</span>
            <strong>Patent pending</strong>
          </div>
          <div className="hero-intel__slides">
            {heroSlides.map((slide, index) => (
              <article
                className="hero-intel__slide"
                key={slide.title}
                style={{ animationDelay: `${index * 12}s` }}
              >
                <span className="eyebrow">{slide.eyebrow}</span>
                <h2>{slide.title}</h2>
                <p>{slide.copy}</p>
                <small>{slide.detail}</small>
              </article>
            ))}
          </div>
          <div className="hero-intel__meters" aria-hidden="true">
            {heroSlides.map((slide, index) => (
              <span key={slide.eyebrow} style={{ animationDelay: `${index * 12}s` }} />
            ))}
          </div>
        </aside>
      </section>

      <section className="public-section">
        <div className="section-inner">
          <div className="section-header">
            <span className="eyebrow">Client Experience</span>
            <h2>A premium path from first look to active market monitoring.</h2>
            <p>
              The experience stays simple on the surface: create an account, confirm your email,
              understand the risk, choose a plan, and prepare the workspace with the discipline a
              serious market-tracking product deserves.
            </p>
          </div>

          <div className="operating-lanes">
            {hybridGates.map((gate) => {
              const Icon = gate.icon;
              return (
                <article className="operating-lane" key={gate.label}>
                  <Icon aria-hidden="true" size={30} />
                  <h3>{gate.title}</h3>
                  <p>{gate.copy}</p>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      <section className="public-section public-section--plans">
        <div className="section-inner">
          <div className="section-header">
            <span className="eyebrow">Monthly Packages</span>
            <h2>Start free. Scale when the operating window matters.</h2>
            <p>
              Preview the system with a verified account, then move into a daily or continuous
              package when your market routine needs more availability.
            </p>
          </div>

          <div className="plan-grid plan-grid--home">
            {planCards.slice(0, 3).map((plan) => {
              const Icon = plan.icon;
              return (
                <article className="plan-card" key={plan.code}>
                  <div className="plan-card__head">
                    <Icon aria-hidden="true" size={24} />
                    <div>
                      <span>{plan.runtime}</span>
                      <h3>{plan.name}</h3>
                    </div>
                  </div>
                  <div className="plan-card__price">
                    <strong>{plan.price}</strong>
                    <span>{plan.cadence}</span>
                  </div>
                  <p>{plan.bestFor}</p>
                  <ul>
                    {plan.includes.slice(0, 3).map((item) => (
                      <li key={item}>
                        <CheckCircle2 aria-hidden="true" size={16} />
                        <span>{item}</span>
                      </li>
                    ))}
                  </ul>
                </article>
              );
            })}
          </div>
        </div>
      </section>

      <section className="public-section public-section--solid">
        <div className="section-inner depth-tabs">
          <div className="depth-tabs__rail" aria-label="Selection states">
            {hybridGates.map((gate, index) => {
              const Icon = gate.icon;
              return (
                <div className={index === 1 ? "depth-tab is-active" : "depth-tab"} key={gate.label}>
                  <Icon aria-hidden="true" size={20} />
                  <span>
                    <strong>{gate.label}</strong>
                    <small>{gate.title}</small>
                  </span>
                </div>
              );
            })}
          </div>

          <div className="depth-tabs__detail">
            <span className="eyebrow">Built For Market Awareness</span>
            <h3>Polished for clients. Disciplined in operation. Quiet where it should be.</h3>
            <p>
              808Fx combines disciplined analytics, in-house intelligence, measured access
              controls, and a premium onboarding experience without exposing sensitive system
              internals to the public.
            </p>
            <div className="page-actions">
              <Link className="command-link command-link--solid" href="/pricing">
                <span>View Access Options</span>
                <ArrowRight aria-hidden="true" size={16} />
              </Link>
              <Link className="command-link command-link--ghost" href="/risk-disclosure">
                <Sparkles aria-hidden="true" size={16} />
                <span>Review Protection Terms</span>
              </Link>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
