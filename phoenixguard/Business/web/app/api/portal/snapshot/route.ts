import { NextResponse } from "next/server";
import { getPortalSnapshot } from "@/lib/business-api";
import { readServerSessionTokens } from "@/lib/session-security";

export async function GET() {
  const { accessToken, connectorToken } = await readServerSessionTokens();
  const snapshot = await getPortalSnapshot({ accessToken, connectorToken });
  return NextResponse.json(snapshot);
}
