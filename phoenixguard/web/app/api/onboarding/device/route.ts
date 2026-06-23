import { NextRequest, NextResponse } from "next/server";
import { CONNECTOR_TOKEN_COOKIE } from "@/lib/business-api";
import {
  backendRequest,
  csrfFailure,
  proxyError,
  readJsonBody,
  setSessionCookie,
  validateMutatingRequest
} from "@/lib/session-security";

type DeviceRegisterResponse = {
  connector_token?: string;
};

export async function POST(request: NextRequest) {
  if (!validateMutatingRequest(request)) {
    return csrfFailure();
  }

  const body = await readJsonBody(request);
  const result = await backendRequest<DeviceRegisterResponse>("/v1/device/register", {
    method: "POST",
    body
  });
  if (!result.ok || !result.data?.connector_token) {
    return proxyError(result);
  }

  const response = NextResponse.json(result.data, { status: 201 });
  setSessionCookie(response, request, CONNECTOR_TOKEN_COOKIE, result.data.connector_token);
  return response;
}
