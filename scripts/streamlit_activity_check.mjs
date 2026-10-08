import { chromium } from "playwright";

const appUrl = process.env.STREAMLIT_APP_URL;
const navigationTimeoutMs = 60_000;
const readyTimeoutMs = 75_000;
const wakeTimeoutMs = 150_000;
const pollIntervalMs = 250;
const auditStartedAt = new Date().toISOString();
const startedAt = Date.now();

class HealthCheckError extends Error {
    constructor(diagnostic) {
        super(diagnostic);
        this.diagnostic = diagnostic;
    }
}

function finish(result, diagnostic) {
    console.log(
        JSON.stringify({
            timestamp: auditStartedAt,
            result,
            duration_ms: Date.now() - startedAt,
            diagnostic,
        }),
    );
}

function fail(diagnostic) {
    finish("failure", diagnostic);
    process.exitCode = 1;
}

async function waitForCondition(condition, timeoutMs, diagnostic) {
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
        if (condition()) {
            return;
        }
        await new Promise((resolve) => setTimeout(resolve, pollIntervalMs));
    }
    throw new HealthCheckError(diagnostic);
}

function remainingTime(deadline, diagnostic) {
    const remainingMs = deadline - Date.now();
    if (remainingMs <= 0) {
        throw new HealthCheckError(diagnostic);
    }
    return remainingMs;
}

function sleepingButton(page) {
    return page.getByRole("button", {
        name: /^(yes, get this app back up!|wake up|despertar|reativar)$/i,
    });
}

async function wakeIfNeeded(page) {
    const button = sleepingButton(page);
    if ((await button.count()) === 0) {
        return false;
    }

    try {
        await button.first().click({ timeout: 10_000 });
        await button.first().waitFor({ state: "hidden", timeout: wakeTimeoutMs });
    } catch {
        throw new HealthCheckError("HIBERNATION_WAKE_NOT_CONFIRMED");
    }
    return true;
}

async function waitForInterface(page, timeoutMs) {
    try {
        await page.waitForFunction(
            () => {
                const app = document.querySelector(
                    '[data-testid="stApp"], .stApp, [data-testid="stAppViewContainer"]',
                );
                return document.readyState === "complete" && Boolean(app);
            },
            undefined,
            { timeout: timeoutMs },
        );
    } catch {
        throw new HealthCheckError("STREAMLIT_INTERFACE_NOT_READY");
    }
}

async function waitForAnonymousLogin(page, timeoutMs) {
    try {
        await page
            .getByRole("button", { name: /^entrar com gmail$/i })
            .waitFor({ state: "visible", timeout: timeoutMs });
    } catch {
        throw new HealthCheckError("ANONYMOUS_LOGIN_NOT_IDENTIFIED");
    }
}

async function run() {
    if (!appUrl) {
        throw new HealthCheckError("APP_URL_NOT_CONFIGURED");
    }

    let target;
    try {
        target = new URL(appUrl);
    } catch {
        throw new HealthCheckError("APP_URL_INVALID");
    }
    if (target.protocol !== "https:") {
        throw new HealthCheckError("APP_URL_MUST_USE_HTTPS");
    }

    let browser;
    try {
        browser = await chromium.launch({ headless: true });
    } catch {
        throw new HealthCheckError("BROWSER_LAUNCH_FAILED");
    }

    try {
        const socketState = { opened: 0, framesReceived: 0 };
        const context = await browser.newContext({ serviceWorkers: "block" });
        const page = await context.newPage();

        page.on("websocket", (socket) => {
            if (!/\/_stcore\/stream(?:\?|$)/.test(socket.url())) {
                return;
            }
            socketState.opened += 1;
            socket.on("framereceived", () => {
                // Frame bytes are deliberately neither retained nor logged.
                socketState.framesReceived += 1;
            });
        });

        try {
            await page.goto(target.toString(), {
                waitUntil: "domcontentloaded",
                timeout: navigationTimeoutMs,
            });
        } catch {
            throw new HealthCheckError("NAVIGATION_FAILED");
        }

        const awakened = await wakeIfNeeded(page);
        const sessionDeadline = Date.now() + (awakened ? wakeTimeoutMs : readyTimeoutMs);
        await waitForInterface(
            page,
            remainingTime(sessionDeadline, "STREAMLIT_INTERFACE_NOT_READY"),
        );
        await waitForAnonymousLogin(
            page,
            remainingTime(sessionDeadline, "ANONYMOUS_LOGIN_NOT_IDENTIFIED"),
        );
        await waitForCondition(
            () => socketState.opened > 0,
            remainingTime(sessionDeadline, "STREAMLIT_WEBSOCKET_NOT_OPENED"),
            "STREAMLIT_WEBSOCKET_NOT_OPENED",
        );
        await waitForCondition(
            () => socketState.framesReceived > 0,
            remainingTime(sessionDeadline, "STREAMLIT_WEBSOCKET_NO_FRAMES"),
            "STREAMLIT_WEBSOCKET_NO_FRAMES",
        );

        finish("success", awakened ? "AWAKENED_AND_READY" : "READY");
    } finally {
        await browser.close().catch(() => undefined);
    }
}

run().catch((error) => {
    fail(error instanceof HealthCheckError ? error.diagnostic : "CHECKER_INTERNAL_ERROR");
});
