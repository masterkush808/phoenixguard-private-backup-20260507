import { mkdir } from "node:fs/promises";
import http from "node:http";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { spawn, spawnSync } from "node:child_process";
import { chromium } from "playwright";

process.env.PW_TEST_SCREENSHOT_NO_FONTS_READY = "1";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const webRoot = path.resolve(__dirname, "..");
const projectRoot = path.resolve(webRoot, "..", "..");
const runtimeDir = process.env.PHOENIXGUARD_RUNTIME_DIR || path.join(projectRoot, "runtime", "live");
const screenshotDir = path.join(runtimeDir, "web-smoke");
const routes = ["/", "/pricing", "/risk-disclosure", "/login", "/app", "/app/downloads", "/app/tracker", "/admin"];
const trackerPath = process.env.NEXT_PUBLIC_TRACKER_DASHBOARD_URL || "http://127.0.0.1:8793/v3/mobile/window-tracker/dashboard/pocket-live-8788";
const viewports = [
  { name: "desktop", width: 1440, height: 980 },
  { name: "mobile", width: 390, height: 844 }
];
let activeServerChild = null;

function killProcessTree(pid) {
  if (!pid) {
    return;
  }

  if (process.platform === "win32") {
    spawnSync("taskkill", ["/pid", String(pid), "/t", "/f"], { stdio: "ignore" });
    return;
  }

  try {
    process.kill(pid, "SIGTERM");
  } catch {
  }
}

process.on("exit", () => {
  if (activeServerChild?.pid) {
    killProcessTree(activeServerChild.pid);
  }
});

async function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      const port = typeof address === "object" && address ? address.port : 3100;
      server.close(() => resolve(String(port)));
    });
  });
}

async function waitForUrl(url, timeoutMs = 45_000) {
  const started = Date.now();
  while (Date.now() - started < timeoutMs) {
    if (await canReach(url)) {
      await new Promise((resolve) => setTimeout(resolve, 750));
      if (await canReach(url)) {
        return;
      }
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Timed out waiting for ${url}`);
}

function canReach(url) {
  return new Promise((resolve) => {
    const request = http.get(url, (response) => {
      response.resume();
      resolve(response.statusCode >= 200 && response.statusCode < 500);
    });
    request.on("error", () => resolve(false));
    request.setTimeout(1000, () => {
      request.destroy();
      resolve(false);
    });
  });
}

async function startServerIfNeeded() {
  const explicitBaseUrl = process.env.WEB_BASE_URL;
  if (explicitBaseUrl) {
    await waitForUrl(explicitBaseUrl);
    return { baseUrl: explicitBaseUrl.replace(/\/+$/, ""), child: null };
  }

  const nextBin = path.join(webRoot, "node_modules", "next", "dist", "bin", "next");
  const runFreshBuild = process.env.WEB_SMOKE_SKIP_BUILD !== "1";
  if (runFreshBuild) {
    const build = spawnSync(process.execPath, [nextBin, "build"], {
      cwd: webRoot,
      env: {
        ...process.env,
        NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000"
      },
      stdio: "inherit"
    });
    if (build.status !== 0) {
      throw new Error(`Next.js smoke build failed with exit code ${build.status ?? "unknown"}`);
    }
  }

  const port = process.env.WEB_SMOKE_PORT || (await getFreePort());
  const baseUrl = `http://127.0.0.1:${port}`;
  if (await canReach(baseUrl)) {
    return { baseUrl, child: null };
  }

  const mode = process.env.WEB_SMOKE_NEXT_MODE || (runFreshBuild ? "start" : "dev");
  const child = spawn(
    process.execPath,
    [nextBin, mode, "--hostname", "127.0.0.1", "--port", port],
    {
      cwd: webRoot,
      env: {
        ...process.env,
        NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000"
      },
      shell: false,
      stdio: "inherit"
    }
  );
  activeServerChild = child;
  await waitForUrl(baseUrl);
  return { baseUrl, child };
}

function isIgnorableConsoleError(text) {
  return (
    text.includes("ERR_CONNECTION_REFUSED") ||
    text.includes("Failed to load resource") ||
    text.includes("favicon.ico") ||
    (text.includes("hydrated but some attributes") && text.includes("caret-color"))
  );
}

async function stopServer(child) {
  if (!child?.pid) {
    return;
  }
  killProcessTree(child.pid);
  activeServerChild = null;
}

async function assertNoLayoutOverflow(page, route, viewportName) {
  const overflow = await page.evaluate(() => {
    const rootOverflow = document.documentElement.scrollWidth - window.innerWidth;
    const offenders = Array.from(
      document.querySelectorAll(
        ".command-link, .portal-nav__item, .state-panel, .depth-tab, .tracker-launchbar, .gate-ledger__row, .service-locked-panel, .form-status, .login-panel"
      )
    )
      .map((element) => {
        const htmlElement = element;
        return {
          text: htmlElement.textContent?.replace(/\s+/g, " ").trim().slice(0, 80),
          overflow: htmlElement.scrollWidth - htmlElement.clientWidth
        };
      })
      .filter((item) => item.overflow > 3);
    return { rootOverflow, offenders };
  });
  if (overflow.rootOverflow > 3 || overflow.offenders.length > 0) {
    throw new Error(
      `${route} ${viewportName} overflow: root=${overflow.rootOverflow}, offenders=${JSON.stringify(overflow.offenders)}`
    );
  }
}

async function assertAmbientImagery(page, route, viewportName) {
  const backgroundImage = await page.evaluate(() => {
    const slide = document.querySelector(".ambient-background__slide");
    return slide ? window.getComputedStyle(slide).backgroundImage : "";
  });
  if (!backgroundImage.includes("/css-control/")) {
    throw new Error(`${route} ${viewportName}: missing css-control ambient image`);
  }
}

async function main() {
  await mkdir(screenshotDir, { recursive: true });
  const { baseUrl, child } = await startServerIfNeeded();
  const browser = await chromium.launch();
  const failures = [];

  try {
    for (const viewport of viewports) {
      const context = await browser.newContext({ viewport });

      for (const route of routes) {
        const page = await context.newPage();
        const consoleErrors = [];
        page.on("console", (message) => {
          if (message.type() === "error" && !isIgnorableConsoleError(message.text())) {
            consoleErrors.push(message.text());
          }
        });
        page.on("pageerror", (error) => consoleErrors.push(error.message));
        await page.goto(`${baseUrl}${route}`, { waitUntil: "networkidle" });
        await page.screenshot({
          path: path.join(screenshotDir, `${viewport.name}-${route.replaceAll("/", "_") || "home"}.png`),
          fullPage: true,
          timeout: 30_000
        });
        await assertNoLayoutOverflow(page, route, viewport.name);
        if (["/", "/pricing", "/risk-disclosure", "/login"].includes(route)) {
          await assertAmbientImagery(page, route, viewport.name);
        }
        const h1OrH2 = await page.locator("h1, h2").first().textContent();
        if (!h1OrH2?.trim()) {
          failures.push(`${route} ${viewport.name}: missing heading`);
        }
        if (route === "/app") {
          for (const id of [
            "registration",
            "email-confirmation",
            "license",
            "broker-binding",
            "disclosure",
            "device-health",
            "tracker-launch"
          ]) {
            if (!(await page.getByTestId(id).isVisible())) {
              failures.push(`${route} ${viewport.name}: missing ${id}`);
            }
          }
          for (const id of [
            "disclosure-accept-submit",
            "checkout-start-submit",
            "broker-binding-form",
            "device-register-form",
            "open-tracker-gui"
          ]) {
            if (!(await page.getByTestId(id).isVisible())) {
              failures.push(`${route} ${viewport.name}: missing onboarding control ${id}`);
            }
          }
          const licenseText = await page.getByTestId("license-status").textContent({ timeout: 5000 }).catch(() => "");
          if (/active/i.test(licenseText || "")) {
            failures.push(`${route} ${viewport.name}: anonymous smoke context must not show active license`);
          }
        }
        if (route === "/app/tracker") {
          if (!(await page.getByTestId("tracker-launch-state").isVisible())) {
            failures.push(`${route} ${viewport.name}: missing tracker launch state`);
          }
          const locked = await page.getByTestId("tracker-frame-locked").isVisible().catch(() => false);
          const frame = await page.getByTestId("tracker-frame").isVisible().catch(() => false);
          if (!locked && !frame) {
            failures.push(`${route} ${viewport.name}: missing tracker frame or locked panel`);
          }
          if (frame) {
            const trackerSrc = await page.getByTestId("tracker-frame").getAttribute("src");
            if (trackerSrc !== trackerPath && !trackerSrc?.includes(trackerPath)) {
              failures.push(`${route} ${viewport.name}: iframe does not target ${trackerPath}; got ${trackerSrc}`);
            }
          }
        }
        if (route === "/app/downloads" && !(await page.getByTestId("release-download-table").isVisible())) {
          failures.push(`${route} ${viewport.name}: missing release download table`);
        }
        if (consoleErrors.length > 0) {
          failures.push(`${route} ${viewport.name}: console errors: ${consoleErrors.join(" | ")}`);
        }
        await page.close();
      }
      await context.close();
    }
  } finally {
    try {
      await browser.close();
    } finally {
      await stopServer(child);
    }
  }

  if (failures.length > 0) {
    throw new Error(failures.join("\n"));
  }
  console.log(`Smoke checks passed for ${routes.length} routes at ${baseUrl}`);
  console.log(`Screenshots written to ${screenshotDir}`);
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
