"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { clearClientSession } from "@/lib/client-session";
import { portalNav } from "@/lib/site-data";

type PortalShellProps = {
  children: ReactNode;
  eyebrow?: string;
  title?: string;
};

export function PortalShell({
  children,
  eyebrow = "808Fx Standard Hybrid System",
  title = "Customer Workspace"
}: PortalShellProps) {
  const pathname = usePathname();

  return (
    <div className="portal-shell" data-testid="portal-shell">
      <aside className="portal-rail">
        <Link className="brand-lockup portal-rail__brand" href="/app">
          <span className="brand-mark">
            <ShieldCheck aria-hidden="true" size={18} />
          </span>
          <span>
            <strong>808Fx Standard Hybrid</strong>
            <small>powered by PhoenixGuard Engine</small>
          </span>
        </Link>

        <nav className="portal-nav" aria-label="Portal navigation">
          {portalNav.map((item) => {
            const Icon = item.icon;
            const active =
              item.href === "/app" ? pathname === "/app" : pathname.startsWith(item.href);

            return (
              <Link
                className={active ? "portal-nav__item is-active" : "portal-nav__item"}
                href={item.href}
                key={item.href}
              >
                <Icon aria-hidden="true" size={18} />
                <span>{item.label}</span>
              </Link>
            );
          })}
        </nav>

        <div className="portal-rail__status">
          <span>Access</span>
          <strong>Protected setup</strong>
          <small>Services open after account, risk, payment, and device checks.</small>
        </div>
      </aside>

      <div className="portal-workspace">
        <header className="portal-topbar">
          <div>
            <span className="eyebrow">{eyebrow}</span>
            <h1>{title}</h1>
          </div>
          <Link
            className="icon-command"
            href="/login"
            aria-label="Leave portal"
            data-testid="logout-button"
            onClick={clearClientSession}
          >
            <LogOut aria-hidden="true" size={18} />
          </Link>
        </header>
        {children}
      </div>
    </div>
  );
}
