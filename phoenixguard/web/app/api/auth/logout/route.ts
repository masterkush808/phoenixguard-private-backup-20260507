import { NextRequest, NextResponse } from "next/server";
import { clearSessionCookies, csrfFailure, validateMutatingRequest } from "@/lib/session-security";

export async function POST(request: NextRequest) {
  if (!validateMutatingRequest(request)) {
    return csrfFailure();
  }

  const response = NextResponse.json({ ok: true });
  clearSessionCookies(response, request);
  return response;
}
