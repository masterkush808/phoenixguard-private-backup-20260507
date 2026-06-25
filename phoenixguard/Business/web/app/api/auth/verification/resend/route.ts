import { NextRequest, NextResponse } from "next/server";
import { backendRequest, csrfFailure, proxyError, readJsonBody, validateMutatingRequest } from "@/lib/session-security";

export async function POST(request: NextRequest) {
  if (!validateMutatingRequest(request)) {
    return csrfFailure();
  }

  const body = await readJsonBody(request);
  const result = await backendRequest("/v1/auth/verification/resend", {
    method: "POST",
    body
  });
  if (!result.ok) {
    return proxyError(result);
  }

  return NextResponse.json(result.data);
}
