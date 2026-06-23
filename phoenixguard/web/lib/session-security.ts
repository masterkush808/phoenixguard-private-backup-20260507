import { NextRequest, NextResponse } from "next/server";
import { cookies } from "next/headers";
import { ACCESS_TOKEN_COOKIE, CONNECTOR_TOKEN_COOKIE } from "@/lib/business-api";
import { CSRF_COOKIE, isHttpsRequest } from "@/lib/security-constants";
import { getApiBaseUrl } from "@/lib/tracker";

const sessionMaxAge = 60 * 60 * 24 * 14;

export type BackendResult<T = unknown> = {
  ok: boolean;
  status: number;
  data: T | null;
  error: string;
  retryAfter?: string;
};

export function csrfFailure() {
  return NextResponse.json(
    { error: "csrf_validation_failed", detail: "Refresh the page and try again." },
    { status: 403 }
  );
}

export function validateMutatingRequest(request: NextRequest) {
  const origin = request.headers.get("origin");
  const hostOrigin = `${request.nextUrl.protocol}//${request.headers.get("host") || request.nextUrl.host}`;
  if (origin && origin !== request.nextUrl.origin && origin !== hostOrigin) {
    return false;
  }

  const supplied = request.headers.get("x-csrf-token") || "";
  const expected = request.cookies.get(CSRF_COOKIE)?.value || readCookieHeader(request, CSRF_COOKIE);
  return Boolean(supplied && expected && supplied === expected);
}

function readCookieHeader(request: NextRequest, name: string) {
  const prefix = `${encodeURIComponent(name)}=`;
  return (request.headers.get("cookie") || "")
    .split(";")
    .map((part) => part.trim())
    .find((part) => part.startsWith(prefix))
    ?.slice(prefix.length) || "";
}

export function setSessionCookie(response: NextResponse, request: NextRequest, name: string, value: string) {
  response.cookies.set({
    name,
    value,
    httpOnly: true,
    secure: isHttpsRequest(request),
    sameSite: "lax",
    path: "/",
    maxAge: sessionMaxAge
  });
}

export function clearSessionCookies(response: NextResponse, request: NextRequest) {
  for (const name of [ACCESS_TOKEN_COOKIE, CONNECTOR_TOKEN_COOKIE]) {
    response.cookies.set({
      name,
      value: "",
      httpOnly: true,
      secure: isHttpsRequest(request),
      sameSite: "lax",
      path: "/",
      maxAge: 0
    });
  }
}

export async function readServerSessionTokens() {
  const cookieStore = await cookies();
  return {
    accessToken: cookieStore.get(ACCESS_TOKEN_COOKIE)?.value || null,
    connectorToken: cookieStore.get(CONNECTOR_TOKEN_COOKIE)?.value || null
  };
}

export async function readJsonBody<T = Record<string, unknown>>(request: NextRequest): Promise<T> {
  try {
    return (await request.json()) as T;
  } catch {
    return {} as T;
  }
}

export async function backendRequest<T = unknown>(
  path: string,
  {
    method = "GET",
    body,
    token,
    connectorToken
  }: {
    method?: string;
    body?: unknown;
    token?: string | null;
    connectorToken?: string | null;
  } = {}
): Promise<BackendResult<T>> {
  const headers: Record<string, string> = {};
  const resolvedToken = connectorToken || token;
  if (resolvedToken) {
    headers.Authorization = `Bearer ${resolvedToken}`;
  }
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  try {
    const response = await fetch(`${getApiBaseUrl()}${path}`, {
      method,
      cache: "no-store",
      headers,
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    const text = await response.text();
    const parsed = text ? safeParseJson<T>(text) : null;
    return {
      ok: response.ok,
      status: response.status,
      data: response.ok ? parsed : null,
      error: response.ok ? "" : extractError(parsed, text, response.status),
      retryAfter: response.headers.get("retry-after") || undefined
    };
  } catch (error) {
    return {
      ok: false,
      status: 0,
      data: null,
      error: error instanceof Error ? error.message : "Service request failed."
    };
  }
}

export function proxyError(result: BackendResult) {
  const response = NextResponse.json(
    { error: result.error || "request_failed", detail: result.error || "Request failed." },
    { status: result.status || 502 }
  );
  if (result.retryAfter) {
    response.headers.set("Retry-After", result.retryAfter);
  }
  return response;
}

function safeParseJson<T>(value: string): T | null {
  try {
    return JSON.parse(value) as T;
  } catch {
    return null;
  }
}

function extractError(parsed: unknown, fallback: string, status: number) {
  if (parsed && typeof parsed === "object" && "detail" in parsed) {
    const detail = (parsed as { detail: unknown }).detail;
    return typeof detail === "string" ? detail : JSON.stringify(detail);
  }
  if (parsed && typeof parsed === "object" && "error" in parsed) {
    return String((parsed as { error: unknown }).error);
  }
  return fallback || `HTTP ${status}`;
}
