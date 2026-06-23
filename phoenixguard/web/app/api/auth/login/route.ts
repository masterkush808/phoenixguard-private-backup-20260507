import { NextRequest, NextResponse } from "next/server";
import { ACCESS_TOKEN_COOKIE } from "@/lib/business-api";
import {
  backendRequest,
  csrfFailure,
  proxyError,
  readJsonBody,
  setSessionCookie,
  validateMutatingRequest
} from "@/lib/session-security";

type LoginResponse = {
  access_token?: string;
  customer?: { email_verified?: boolean };
  user?: { email_verified?: boolean };
};

export async function POST(request: NextRequest) {
  if (!validateMutatingRequest(request)) {
    return csrfFailure();
  }

  const body = await readJsonBody(request);
  const result = await backendRequest<LoginResponse>("/v1/auth/login", {
    method: "POST",
    body
  });
  if (!result.ok || !result.data?.access_token) {
    return proxyError(result);
  }

  const account = result.data.customer || result.data.user;
  if (account?.email_verified === false) {
    return NextResponse.json(
      { error: "email_verification_required", detail: "Confirm your email before entering the workspace." },
      { status: 403 }
    );
  }

  const response = NextResponse.json(result.data);
  setSessionCookie(response, request, ACCESS_TOKEN_COOKIE, result.data.access_token);
  return response;
}
