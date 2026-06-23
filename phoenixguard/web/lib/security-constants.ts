import type { NextRequest } from "next/server";

export const CSRF_COOKIE = "pg_csrf_token";

export function isHttpsRequest(request: NextRequest) {
  return request.nextUrl.protocol === "https:" || request.headers.get("x-forwarded-proto") === "https";
}
