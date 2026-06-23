"use client";

import { FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { MailCheck, UserPlus } from "lucide-react";
import { csrfHeaders } from "@/lib/client-session";

export function RegisterForm() {
  const router = useRouter();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [verificationToken, setVerificationToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const token = params.get("verify_token") || params.get("token") || "";
    if (!token) {
      return;
    }
    setVerificationToken(token);
    setMessage("Confirmation code loaded from the email link. Confirm it to continue.");
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    setError("");
    try {
      const response = await fetch("/api/auth/register", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...csrfHeaders() },
        body: JSON.stringify({ email, full_name: fullName, password })
      });
      const payload = await response.json();
      if (!response.ok) {
        setError(typeof payload.detail === "string" ? payload.detail : "Registration failed. Review your details and try again.");
        return;
      }
      const localToken = payload.email_verification?.development_token;
      if (payload.email_verification?.sent) {
        setMessage("Confirmation email sent. Verify your email before this account can continue.");
      } else if (localToken) {
        setVerificationToken(String(localToken));
        setMessage("Local test mode prepared a confirmation code. Confirm it below to continue.");
      } else {
        setError("Account created, but email delivery is not connected yet. Access remains locked until confirmation is available.");
      }
    } catch {
      setError("Registration is temporarily unavailable. Please try again once the service is connected.");
    } finally {
      setBusy(false);
    }
  }

  async function verifyEmail(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setMessage("");
    setError("");
    let response = await fetch("/api/auth/verify-email", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify({ token: verificationToken })
    });

    if (!response.ok && response.status === 422) {
      response = await fetch("/api/auth/verify-email", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", ...csrfHeaders() },
        body: JSON.stringify({ verification_token: verificationToken })
      });
    }

    if (!response.ok) {
      const payload = await response.json().catch(() => null);
      setError(payload?.detail || "Email verification failed.");
      setBusy(false);
      return;
    }
    const payload = await response.json().catch(() => null);

    if (payload?.customer?.email_verified === false) {
      setMessage("We received the code, but the email is not confirmed yet.");
      setBusy(false);
      return;
    }

    setMessage("Email confirmed. Opening your protected setup workspace.");
    router.push("/app");
    router.refresh();
    setBusy(false);
  }

  async function resendVerification() {
    if (!email) {
      setError("Enter the account email before requesting another confirmation code.");
      return;
    }
    setBusy(true);
    setMessage("");
    setError("");
    const response = await fetch("/api/auth/verification/resend", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json", ...csrfHeaders() },
      body: JSON.stringify({ email })
    });
    const payload = await response.json().catch(() => null);
    if (!response.ok) {
      setError(payload?.detail || "Confirmation resend is temporarily unavailable.");
      setBusy(false);
      return;
    }
    const localToken = payload?.email_verification?.development_token;
    if (localToken) {
      setVerificationToken(String(localToken));
      setMessage("Local test mode refreshed the confirmation code.");
    } else if (payload?.email_verification?.sent) {
      setMessage("Confirmation email sent again.");
    } else {
      setMessage("If the account is eligible, a confirmation email will be sent.");
    }
    setBusy(false);
  }

  return (
    <>
      <form className="login-panel login-panel--register" data-testid="registration-form" onSubmit={submit}>
        <div className="login-panel__header">
          <span className="eyebrow">Create Verified Account</span>
          <h2>Build your access identity.</h2>
          <p>Begin with a confirmed email before any protected service can open.</p>
        </div>
        <label>
          Full name
          <input
            data-testid="registration-name"
            onChange={(event) => setFullName(event.target.value)}
            placeholder="Full legal name"
            required
            autoComplete="name"
            value={fullName}
          />
        </label>
        <label>
          Email
          <input
            data-testid="registration-email"
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
            data-testid="registration-password"
            minLength={15}
            maxLength={128}
            onChange={(event) => setPassword(event.target.value)}
            placeholder="Create a secure passphrase"
            required
            type="password"
            autoComplete="new-password"
            value={password}
          />
        </label>
        <button className="command-link command-link--solid" data-testid="registration-submit" disabled={busy} type="submit">
          <UserPlus aria-hidden="true" size={16} />
          <span>{busy ? "Creating" : "Create Account"}</span>
        </button>
      </form>

      <form className="login-panel login-panel--verification" data-testid="email-verification-form" onSubmit={verifyEmail}>
        <div className="login-panel__header">
          <span className="eyebrow">Confirm Email Before Access</span>
          <h2>Activate the account gate.</h2>
          <p>Paste the confirmation code sent to your inbox to continue safely.</p>
        </div>
        <label>
          Confirmation code
          <input
            data-testid="email-verification-token"
            onChange={(event) => setVerificationToken(event.target.value)}
            placeholder="Paste the code from your email"
            required
            autoComplete="one-time-code"
            value={verificationToken}
          />
        </label>
        <div className="verification-actions">
          <button
            className="command-link command-link--solid"
            data-testid="email-verification-submit"
            disabled={busy}
            type="submit"
          >
            <MailCheck aria-hidden="true" size={16} />
            <span>{busy ? "Verifying" : "Confirm Email"}</span>
          </button>
          <button
            className="command-link command-link--ghost"
            data-testid="email-verification-resend"
            disabled={busy}
            onClick={() => void resendVerification()}
            type="button"
          >
            <MailCheck aria-hidden="true" size={16} />
            <span>Resend Code</span>
          </button>
        </div>
        {message ? (
          <p className="form-success">
            <MailCheck aria-hidden="true" size={16} /> {message}
          </p>
        ) : null}
        {error ? <p className="form-error">{error}</p> : null}
      </form>
    </>
  );
}
