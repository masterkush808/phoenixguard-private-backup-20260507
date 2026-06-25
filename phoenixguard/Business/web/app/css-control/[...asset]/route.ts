import { readFile } from "node:fs/promises";
import path from "node:path";

const allowedAssets = new Set([
  "landing-transition-market-vision.png",
  "landing-transition-market-vision-alt.png",
  "landing-transition-lifestyle-suite.png",
  "landing-transition-lifestyle-travel.png"
]);

const assetRoot = path.resolve(
  process.cwd(),
  "..",
  "..",
  "Frontend",
  "assets",
  "share",
  "css-control"
);

type RouteContext = {
  params: Promise<{
    asset: string[];
  }>;
};

export async function GET(_request: Request, context: RouteContext) {
  const { asset } = await context.params;
  const fileName = asset.join("/");

  if (!allowedAssets.has(fileName)) {
    return new Response("Asset not found", { status: 404 });
  }

  const filePath = path.resolve(assetRoot, fileName);

  if (!filePath.startsWith(assetRoot)) {
    return new Response("Asset not found", { status: 404 });
  }

  const bytes = await readFile(filePath);

  return new Response(new Uint8Array(bytes), {
    headers: {
      "Content-Type": "image/png",
      "Cache-Control": "public, max-age=31536000, immutable"
    }
  });
}
