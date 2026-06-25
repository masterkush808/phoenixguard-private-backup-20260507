"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { ArrowRight, CircleAlert, LockKeyhole } from "lucide-react";
import { clearClientSession, csrfHeaders } from "@/lib/client-session";

export function LoginForm() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("Only verified accounts can open the protected workspace.");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("Checking your account...");
    const response = await fetch("/api/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: {
        "Content-Type": "application/json",
        ...csrfHeaders()
      },
      body: JSON.stringify({ email, password })
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      clearClientSession();
      setMessage(payload?.detail || "We could not sign you in. Check your details and try again.");
      setBusy(false);
      return;
    }

    const account = payload?.customer || payload?.user;
    if (account?.email_verified === false) {
      clearClientSession();
      setMessage("Confirm your email before entering the protected workspace.");
      setBusy(false);
      return;
    }
    setMessage(`Signed in as ${account?.email || email}. Opening your workspace.`);
    try {
      router.push(account?.role === "admin" || account?.is_admin ? "/admin" : "/app");
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="login-panel login-panel--signin" data-testid="login-form" onSubmit={submit}>
      <div className="login-panel__header">
        <span className="eyebrow">Returning Client</span>
        <h2>Enter the workspace.</h2>
        <p>Use the verified account connected to your current access plan.</p>
      </div>
      <div className="form-status" role="status">
        <CircleAlert aria-hidden="true" size={16} />
        <span>{message}</span>
      </div>
      <label>
        Email
        <input
          data-testid="login-email"
          name="email"
          onChange={(event) => setEmail(event.target.value)}
          placeholder="customer@example.com"
          required
          type="email"
          autoComplete="email"
          value={email}
        />
      </label>
      <label>
        Password
        <input
          data-testid="login-password"
          name="password"
          minLength={15}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="Minimum 15 characters"
          required
          type="password"
          autoComplete="current-password"
          value={password}
        />
      </label>
      <button className="command-link command-link--solid" data-testid="login-submit" disabled={busy} type="submit">
        <LockKeyhole aria-hidden="true" size={16} />
        <span>{busy ? "Checking" : "Continue"}</span>
        <ArrowRight aria-hidden="true" size={16} />
      </button>
    </form>
  );
}
