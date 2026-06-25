import type { LucideIcon } from "lucide-react";

type StatePanelProps = {
  id?: string;
  label: string;
  value: string;
  detail: string;
  status: string;
  icon: LucideIcon;
  tone?: "good" | "warn" | "blocked" | "info";
};

export function StatePanel({ id, label, value, detail, status, icon: Icon, tone = "info" }: StatePanelProps) {
  return (
    <section className={`state-panel state-panel--${tone}`} data-testid={id}>
      <div className="state-panel__icon">
        <Icon aria-hidden="true" size={19} />
      </div>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{detail}</small>
      </div>
      <em>{status}</em>
    </section>
  );
}
