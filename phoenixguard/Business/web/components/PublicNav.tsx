import Link from "next/link";
import { ArrowRight, ChevronDown, ShieldCheck } from "lucide-react";
import { publicExploreLinks, publicNav } from "@/lib/site-data";

export function PublicNav() {
  return (
    <header className="public-nav" data-testid="public-nav">
      <Link className="brand-lockup" href="/" aria-label="808Fx Standard Hybrid System home">
        <span className="brand-mark">
          <ShieldCheck aria-hidden="true" size={18} />
        </span>
        <span>
          <strong>808Fx Standard Hybrid</strong>
          <small>powered by PhoenixGuard Engine</small>
        </span>
      </Link>

      <nav className="public-nav__links" aria-label="Public navigation">
        {publicNav.map((item) => (
          <Link href={item.href} key={item.href}>
            {item.label}
          </Link>
        ))}
        <details className="public-explore">
          <summary>
            <span>Explore</span>
            <ChevronDown aria-hidden="true" size={14} />
          </summary>
          <div className="public-explore__menu">
            {publicExploreLinks.map((item) => (
              <Link href={item.href} key={item.href}>
                <strong>{item.label}</strong>
                <small>{item.copy}</small>
              </Link>
            ))}
          </div>
        </details>
      </nav>

      <Link className="command-link command-link--solid" href="/login">
        <span>Create Account</span>
        <ArrowRight aria-hidden="true" size={16} />
      </Link>
    </header>
  );
}
