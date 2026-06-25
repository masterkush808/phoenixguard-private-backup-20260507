"use client";

import { ACCESS_TOKEN_COOKIE, CONNECTOR_TOKEN_COOKIE } from "@/lib/business-api";
import { CSRF_COOKIE } from "@/lib/security-constants";

const maxAge = 60 * 60 * 24 * 14;

export function readClientToken(name: string) {
  if (typeof window === "undefined") {
    return null;
  }

  const local = window.localStorage.getItem(name);
  if (local) {
    return local;
  }

  const cookie = document.cookie
    .split("; ")
    .find((item) => item.startsWith(`${encodeURIComponent(name)}=`));
  return cookie ? decodeURIComponent(cookie.split("=").slice(1).join("=")) : null;
}

export function readClientCookie(name: string) {
  if (typeof document === "undefined") {
    return null;
  }
  const cookie = document.cookie
    .split("; ")
    .find((item) => item.startsWith(`${encodeURIComponent(name)}=`));
  return cookie ? decodeURIComponent(cookie.split("=").slice(1).join("=")) : null;
}

export function csrfHeaders(): Record<string, string> {
  const token = readClientCookie(CSRF_COOKIE) || "";
  return token ? { "X-CSRF-Token": token } : {};
}

export function writeClientToken(name: string, value: string) {
  window.localStorage.setItem(name, value);
  document.cookie = `${encodeURIComponent(name)}=${encodeURIComponent(value)}; path=/; max-age=${maxAge}; SameSite=Lax`;
}

export function clearClientSession() {
  for (const name of [ACCESS_TOKEN_COOKIE, CONNECTOR_TOKEN_COOKIE]) {
    window.localStorage.removeItem(name);
    document.cookie = `${encodeURIComponent(name)}=; path=/; max-age=0; SameSite=Lax`;
  }
}
