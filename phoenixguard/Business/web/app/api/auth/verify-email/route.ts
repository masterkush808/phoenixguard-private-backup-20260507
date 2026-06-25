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

type VerifyEmailResponse = {
  access_token?: string;
};

export async function POST(request: NextRequest) {
  if (!validateMutatingRequest(request)) {
    return csrfFailure();
  }

  const body = await readJsonBody(request);
  const result = await backendRequest<VerifyEmailResponse>("/v1/auth/verify-email", {
    method: "POST",
    body
  });
  if (!result.ok || !result.data?.access_token) {
    return proxyError(result);
  }

  const response = NextResponse.json(result.data);
  setSessionCookie(response, request, ACCESS_TOKEN_COOKIE, result.data.access_token);
  return response;
}
