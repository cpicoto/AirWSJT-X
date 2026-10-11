import { test, expect } from "@playwright/test";

test.use({ userAgent: "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36", viewport: { width: 390, height: 844 } });

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.mobile.settings", JSON.stringify({
      myCall: "TEST", myGrid: "CN87", dxCall: "", dxGrid: "", speechEnabled: false,
    }));
  });
});

test("mobile accounts are available and use native aircraft requests for both providers", async ({ page }) => {
  await page.goto("/");
  await expect(page.locator("span").getByText("MOBILE", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByText("Aircraft accounts", { exact: true }).click();
  await page.getByLabel("ADS-B Exchange API key", { exact: true }).fill("fake-mobile-key");
  await page.getByRole("button", { name: "Save ADS-B Exchange", exact: true }).click();
  await expect(page.getByLabel("ADS-B Exchange API key", { exact: true })).toHaveValue("");
  await page.getByLabel("Aircraft source", { exact: true }).selectOption("adsbexchange");
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(page.locator("span").getByText("MOBILE", { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => {
    const calls = (window as unknown as { __mockAircraftRequests: { provider: string }[] }).__mockAircraftRequests;
    return calls?.some(call => call.provider === "adsbexchange");
  })).toBe(true);
  const storage = await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage));
  expect(storage).not.toContain("fake-mobile-key");
});

test("mobile uses a saved 10-second OpenSky polling cadence", async ({ page }) => {
  await page.clock.install();
  await page.addInitScript(() => {
    Object.assign(window, { __mockCredentialStatus: {
      opensky_client_id: "", opensky_configured: false, adsbexchange_configured: false,
      opensky_poll_interval_s: 10, adsbexchange_poll_interval_s: 30,
    } });
  });

  await page.goto("/");
  await expect(page.locator("span").getByText("MOBILE", { exact: true })).toBeVisible();
  const count = () => page.evaluate(() =>
    (window as unknown as { __mockAircraftRequests: unknown[] }).__mockAircraftRequests.length);
  const first = await count();
  await page.clock.runFor(9000);
  expect(await count()).toBe(first);
  await page.clock.runFor(1100);
  expect(await count()).toBe(first + 1);
});

test("mobile pauses for the native cooldown and resumes configured polling after reset", async ({ page }) => {
  await page.clock.install();
  await page.addInitScript(() => {
    Object.assign(window, { __mockAircraftRateLimited: true });
  });
  await page.goto("/");
  await expect(page.getByText("Rate limited — retry in 1800s", { exact: true })).toBeVisible();
  const count = () => page.evaluate(() =>
    (window as unknown as { __mockAircraftRequests: unknown[] }).__mockAircraftRequests.length);
  const first = await count();
  await page.clock.runFor(1799000);
  expect(await count()).toBe(first);
  await page.evaluate(() => Object.assign(window, { __mockAircraftRateLimited: false }));
  await page.clock.runFor(1500);
  await expect(page.locator("span").getByText("MOBILE", { exact: true })).toBeVisible();
  expect(await count()).toBe(first + 1);
  await page.clock.runFor(30000);
  expect(await count()).toBe(first + 2);
});
