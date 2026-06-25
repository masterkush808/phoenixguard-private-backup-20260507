import Link from "next/link";
import { BadgeCheck, LockKeyhole, ShieldCheck, TimerReset } from "lucide-react";
import { PortalShell } from "@/components/PortalShell";
import { StatePanel } from "@/components/StatePanel";
import { getAdminCustomers, getPortalSnapshot } from "@/lib/business-api";
import { getServerTokens } from "@/lib/server-session";

export const dynamic = "force-dynamic";

export default async function AdminPage() {
  const { accessToken, connectorToken } = await getServerTokens();
  const [admin, snapshot] = await Promise.all([
    getAdminCustomers(accessToken),
    getPortalSnapshot({ accessToken, connectorToken })
  ]);
  const activeCount = admin.customers.filter((customer) => customer.license_status === "active").length;
  const disclosureCount = admin.customers.filter((customer) => customer.disclosure_accepted).length;
  const brokerCount = admin.customers.filter((customer) => customer.broker_account_bound).length;

  return (
    <PortalShell eyebrow="PhoenixGuard Admin" title="Commercial Control Desk">
      <main className="portal-main">
        <div className="portal-grid">
          <section className="portal-section">
            <span className="eyebrow">Admin Overview</span>
            <h2>{admin.ok ? "Customer queue is available." : "Admin access is locked."}</h2>
            <p>
              {admin.ok
                ? "Only authorized administrative accounts can review customer readiness."
                : admin.detail}
            </p>
            <div className="admin-list">
              {admin.ok ? (
                <>
                  <div className="admin-row">
                    <BadgeCheck aria-hidden="true" size={19} />
                    <div>
                      <span>Customers</span>
                      <strong>{admin.customers.length}</strong>
                    </div>
                    <em>ready</em>
                  </div>
                  <div className="admin-row">
                    <ShieldCheck aria-hidden="true" size={19} />
                    <div>
                      <span>Active licenses</span>
                      <strong>{activeCount}</strong>
                    </div>
                    <em>active</em>
                  </div>
                  <div className="admin-row">
                    <TimerReset aria-hidden="true" size={19} />
                    <div>
                      <span>Disclosure / broker</span>
                      <strong>
                        {disclosureCount} / {brokerCount}
                      </strong>
                    </div>
                    <em>review</em>
                  </div>
                </>
              ) : (
                <div className="service-locked-panel">
                  <LockKeyhole aria-hidden="true" size={24} />
                  <strong>{admin.status}</strong>
                  <span>{admin.detail}</span>
                  <Link className="command-link command-link--ghost" href="/login">
                    <span>Admin Login</span>
                  </Link>
                </div>
              )}
            </div>
          </section>

          <aside className="panel-board">
            <span className="eyebrow">Policy Lock</span>
            <h2>No customer account can inspect admin queues.</h2>
            <p>
              Customer sessions are kept out of administrative views. The page stays locked unless
              the signed-in account has the correct staff role.
            </p>
          </aside>
        </div>

        {admin.ok ? (
          <section className="portal-section portal-stack">
            <span className="eyebrow">Customers</span>
            <h2>Commercial state by account.</h2>
            <div className="admin-list">
              {admin.customers.map((customer) => (
                <div className="admin-row" key={customer.customer_id}>
                  <BadgeCheck aria-hidden="true" size={19} />
                  <div>
                    <span>{customer.email}</span>
                    <strong>{customer.customer_id}</strong>
                  </div>
                  <em>
                    {customer.license_status} / {customer.broker_account_bound ? "bound" : "unbound"}
                  </em>
                </div>
              ))}
            </div>
          </section>
        ) : null}

        <section className="portal-section portal-stack">
          <span className="eyebrow">Current Session</span>
          <h2>Admin access depends on the signed-in role.</h2>
          <div className="state-grid">
            <StatePanel
              detail="The current account must be marked for administrative review."
              icon={BadgeCheck}
              id="admin-current-user"
              label="Current user"
              status={snapshot.user?.role || "anonymous"}
              tone={snapshot.user?.role === "admin" ? "good" : "blocked"}
              value={snapshot.user?.email || "No signed-in account"}
            />
            <StatePanel
              detail={admin.detail}
              icon={ShieldCheck}
              id="admin-api-state"
              label="Admin access"
              status={admin.status}
              tone={admin.ok ? "good" : "blocked"}
              value={admin.ok ? "Allowed" : "Denied"}
            />
          </div>
        </section>
      </main>
    </PortalShell>
  );
}
