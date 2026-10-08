import { chromium } from "playwright";

const appUrl = process.env.STREAMLIT_APP_URL;
const timeoutMs = 90_000;
const startupTimeoutMs = 45_000;
const auditStartedAt = new Date().toISOString();
const startedAt = Date.now();

let browser;

function finish(result, diagnostic) {
    const durationMs = Date.now() - startedAt;
    console.log(
        JSON.stringify({
            timestamp: auditStartedAt,
            result,
            duration_ms: durationMs,
            diagnostic,
        }),
    );
}

function fail(diagnostic) {
    finish("failure", diagnostic);
    process.exitCode = 1;
}

async function isSleepingPage(page) {
    return page
        .getByRole("button", { name: /^(wake up|despertar|reativar)$/i })
        .count()
        .then((count) => count > 0);
}

async function wakeIfNeeded(page) {
    if (!(await isSleepingPage(page))) {
        return false;
    }

    await page
        .getByRole("button", { name: /^(wake up|despertar|reativar)$/i })
        .first()
        .click({ timeout: 10_000 });
    await page.waitForTimeout(1_000);
    return true;
}

async function verifyStreamlitSession(page, socketState) {
    await page.waitForFunction(
        () => {
            const app = document.querySelector(
                '[data-testid="stApp"], .stApp, [data-testid="stAppViewContainer"]',
            );
            return document.readyState === "complete" && Boolean(app);
        },
        undefined,
        { timeout: startupTimeoutMs },
    );

    // This is intentionally an anonymous check. The application's restricted
    // login UI is the expected healthy surface before OIDC authentication.
    await page
        .getByRole("button", { name: /^entrar com gmail$/i })
        .waitFor({ state: "visible", timeout: startupTimeoutMs });

    await page.waitForFunction(
        () => Boolean(window.WebSocket) && document.querySelectorAll("button").length > 0,
        undefined,
        { timeout: 10_000 },
    );

    await page.waitForFunction(
        () => window.__streamlitActivitySocket?.framesReceived > 0,
        undefined,
        { timeout: startupTimeoutMs },
    );

    if (!socketState.opened || !socketState.streamlitEndpoint) {
        throw new Error("WEBSOCKET_NOT_ESTABLISHED");
    }
}

async function main() {
    if (!appUrl) {
        fail("APP_URL_NOT_CONFIGURED");
        return;
    }

    let target;
    try {
        target = new URL(appUrl);
    } catch {
        fail("APP_URL_INVALID");
        return;
    }
    if (target.protocol !== "https:") {
        fail("APP_URL_MUST_USE_HTTPS");
        return;
    }

    const socketState = { opened: false, streamlitEndpoint: false };
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ serviceWorkers: "block" });
    const page = await context.newPage();
    page.setDefaultTimeout(timeoutMs);
    page.on("websocket", (socket) => {
        const isStreamlitSocket = /\/_stcore\/stream(?:\?|$)/.test(socket.url());
        if (!isStreamlitSocket) {
            return;
        }
        socketState.opened = true;
        socketState.streamlitEndpoint = true;
        socket.on("framereceived", () => {
            // Keep page JavaScript as the synchronization source without
            // retaining or printing any frame or page data.
            void page.evaluate(() => {
                window.__streamlitActivitySocket ??= { framesReceived: 0 };
                window.__streamlitActivitySocket.framesReceived += 1;
            });
        });
    });

    await page.goto(target.toString(), { waitUntil: "domcontentloaded" });
    const awakened = await wakeIfNeeded(page);
    await verifyStreamlitSession(page, socketState);
    finish("success", awakened ? "AWAKENED_AND_READY" : "READY");
}

main()
    .catch((error) => {
        const diagnostic =
            error?.message === "WEBSOCKET_NOT_ESTABLISHED"
                ? "WEBSOCKET_NOT_ESTABLISHED"
                : "STREAMLIT_INTERFACE_OR_SESSION_NOT_READY";
        fail(diagnostic);
    })
    .finally(async () => {
        await browser?.close();
    });
