import { NextRequest, NextResponse } from "next/server";
import {
  backendRequest,
  csrfFailure,
  proxyError,
  readJsonBody,
  readServerSessionTokens,
  validateMutatingRequest
} from "@/lib/session-security";

export async function POST(request: NextRequest) {
  if (!validateMutatingRequest(request)) {
    return csrfFailure();
  }

  const { accessToken } = await readServerSessionTokens();
  const body = await readJsonBody(request);
  const result = await backendRequest<null>("/v1/disclosures/accept", {
    method: "POST",
    token: accessToken,
    body
  });
  if (!result.ok) {
    return proxyError(result);
  }
  return new NextResponse(null, { status: 204 });
}
