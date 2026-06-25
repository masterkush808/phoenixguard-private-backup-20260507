import { readFile } from "node:fs/promises";
import path from "node:path";

const themePath = path.resolve(
  process.cwd(),
  "..",
  "..",
  "Frontend",
  "assets",
  "themes",
  "phoenix_command_tokens.css"
);

export async function GET() {
  const css = await readFile(themePath, "utf8");

  return new Response(css, {
    headers: {
      "Content-Type": "text/css; charset=utf-8",
      "Cache-Control": "public, max-age=3600"
    }
  });
}
