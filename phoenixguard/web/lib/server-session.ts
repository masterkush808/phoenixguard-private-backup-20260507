import { cookies } from "next/headers";
import { ACCESS_TOKEN_COOKIE, CONNECTOR_TOKEN_COOKIE } from "@/lib/business-api";

export async function getServerTokens() {
  const cookieStore = await cookies();

  return {
    accessToken: cookieStore.get(ACCESS_TOKEN_COOKIE)?.value || null,
    connectorToken: cookieStore.get(CONNECTOR_TOKEN_COOKIE)?.value || null
  };
}
