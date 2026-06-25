import { BadgeCheck, CheckCircle2, Clock3, Gauge, MailCheck, ShieldCheck } from "lucide-react";
import { AmbientBackground } from "@/components/AmbientBackground";
import { LoginForm } from "@/components/LoginForm";
import { PublicNav } from "@/components/PublicNav";
import { RegisterForm } from "@/components/RegisterForm";

const accessSignals = [
  {
    icon: MailCheck,
    title: "Confirmed identity",
    copy: "One verified email anchors communication, workspace access, and account recovery."
  },
  {
    icon: ShieldCheck,
    title: "Disclosure first",
    copy: "Risk acknowledgement is required before protected services or connector setup can continue."
  },
  {
    icon: Clock3,
    title: "Clear operating window",
    copy: "Free, focused, and continuous plans keep runtime expectations visible from the start."
  },
  {
    icon: Gauge,
    title: "Readiness checks",
    copy: "The workspace opens only when the account, plan, device, and tracker state are healthy."
  }
];

const accessPlans = [
  { name: "Preview", price: "$0", runtime: "2 hours daily", copy: "Explore the workspace after email confirmation." },
  { name: "Focused", price: "$20", runtime: "6 hours daily", copy: "A measured monthly window for routine market tracking." },
  { name: "Continuous", price: "$100", runtime: "24/7 eligible", copy: "Extended availability for clients who need persistent monitoring." }
];

export default function LoginPage() {
  return (
    <main className="page-shell page-shell--access">
      <AmbientBackground />
      <PublicNav />

      <section className="access-command" aria-labelledby="access-title">
        <div className="access-command__story">
          <span className="hero__product">Verified Client Access</span>
          <h1 id="access-title">Secure entry for the 808Fx Standard Hybrid workspace.</h1>
          <p>
            Create one confirmed identity for plan access, risk acknowledgement, protected
            onboarding, and PhoenixGuard readiness checks.
          </p>

          <div className="access-assurance" aria-label="Access readiness safeguards">
            {accessSignals.map((item) => {
              const Icon = item.icon;
              return (
                <article className="access-assurance__item" key={item.title}>
                  <Icon aria-hidden="true" size={20} />
                  <div>
                    <h2>{item.title}</h2>
                    <p>{item.copy}</p>
                  </div>
                </article>
              );
            })}
          </div>

          <div className="access-planline" aria-label="Available access windows">
            {accessPlans.map((plan) => (
              <article className="access-planline__item" key={plan.name}>
                <span>{plan.name}</span>
                <strong>{plan.price}</strong>
                <em>{plan.runtime}</em>
                <p>{plan.copy}</p>
              </article>
            ))}
          </div>
        </div>

        <aside className="access-command__console" aria-label="Account access actions">
          <div className="access-console__head">
            <div>
              <span className="eyebrow">PhoenixGuard Access Desk</span>
              <h2>Start, return, or confirm in one place.</h2>
            </div>
            <span className="access-console__seal">
              <CheckCircle2 aria-hidden="true" size={16} />
              Email gate active
            </span>
          </div>

          <div className="access-forms">
            <LoginForm />
            <RegisterForm />
          </div>

          <div className="access-console__footer">
            <BadgeCheck aria-hidden="true" size={16} />
            <span>Protected services remain closed until identity, disclosure, plan, and readiness checks are complete.</span>
          </div>
        </aside>
      </section>
    </main>
  );
}
