import { NextRequest, NextResponse } from "next/server";
import { CSRF_COOKIE, isHttpsRequest } from "@/lib/security-constants";

const securityHeaders: Record<string, string> = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Cross-Origin-Opener-Policy": "same-origin",
  "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
  "Content-Security-Policy": [
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "img-src 'self' data: blob:",
    "style-src 'self' 'unsafe-inline'",
    "script-src 'self' 'unsafe-inline'",
    "connect-src 'self' http://127.0.0.1:18181 http://localhost:18181 http://127.0.0.1:18180 http://localhost:18180 http://127.0.0.1:8793 http://localhost:8793",
    "frame-src http://127.0.0.1:18181 http://localhost:18181 http://127.0.0.1:18180 http://localhost:18180 http://127.0.0.1:8793 http://localhost:8793",
    "form-action 'self'"
  ].join("; ")
};

export function proxy(request: NextRequest) {
  const response = NextResponse.next();

  for (const [key, value] of Object.entries(securityHeaders)) {
    response.headers.set(key, value);
  }

  if (!request.cookies.get(CSRF_COOKIE)?.value) {
    response.cookies.set({
      name: CSRF_COOKIE,
      value: crypto.randomUUID(),
      httpOnly: false,
      secure: isHttpsRequest(request),
      sameSite: "strict",
      path: "/",
      maxAge: 60 * 60 * 24
    });
  }

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"]
};
