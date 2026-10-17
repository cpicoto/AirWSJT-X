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
  await expect(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByText("Aircraft accounts", { exact: true }).click();
  await page.getByLabel("ADS-B Exchange API key", { exact: true }).fill("fake-mobile-key");
  await page.getByRole("button", { name: "Save ADS-B Exchange", exact: true }).click();
  await expect(page.getByLabel("ADS-B Exchange API key", { exact: true })).toHaveValue("");
  await page.getByLabel("Aircraft source", { exact: true }).selectOption("adsbexchange");
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => {
    const calls = (window as unknown as { __mockAircraftRequests: { provider: string }[] }).__mockAircraftRequests;
    return calls?.some(call => call.provider === "adsbexchange");
  })).toBe(true);
  const storage = await page.evaluate(() => JSON.stringify(localStorage) + JSON.stringify(sessionStorage));
  expect(storage).not.toContain("fake-mobile-key");
});

test("mobile dump1090 source is selected by saving an address and disabled returns to OpenSky", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByText("dump1090 source", { exact: true }).click();
  await expect(page.getByRole("button", { name: "Use local (127.0.0.1)" })).toHaveCount(0);
  await page.getByLabel("dump1090 IP address or hostname").fill("bad host;");
  await page.getByRole("button", { name: "Save dump1090 source", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("valid IP address or hostname");
  await page.getByLabel("dump1090 IP address or hostname").fill("192.168.2.58");
  await page.getByRole("button", { name: "Save dump1090 source", exact: true }).click();
  await expect(page.getByLabel("Aircraft source", { exact: true })).toHaveValue("dump1090");
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { __mockDump1090Requests?: { host: string }[] }).__mockDump1090Requests?.[0]?.host,
  )).toBe("192.168.2.58");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByText("dump1090 source", { exact: true }).click();
  await page.getByRole("button", { name: "Disable dump1090", exact: true }).click();
  await expect(page.getByLabel("Aircraft source", { exact: true })).toHaveValue("opensky");
  await expect(page.getByLabel("dump1090 IP address or hostname")).toHaveValue("");
  await page.getByLabel("dump1090 IP address or hostname").fill("127.0.0.1");
  await page.getByRole("button", { name: "Save dump1090 source", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("dump1090 source set to 127.0.0.1:30003");
  await expect(page.getByLabel("Aircraft source", { exact: true })).toHaveValue("dump1090");
  await page.getByRole("button", { name: "Done", exact: true }).click();
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { __mockDump1090Requests?: { host: string }[] }).__mockDump1090Requests
      ?.some(call => call.host === "127.0.0.1"),
  )).toBe(true);
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
  await expect(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
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
  await expect(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
  expect(await count()).toBe(first + 1);
  await page.clock.runFor(30000);
  expect(await count()).toBe(first + 2);
});

test("mobile compass tracks device heading and landscape focuses on the dials", async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 });
  await page.addInitScript(() => {
    class FakeDeviceOrientationEvent extends Event {
      alpha: number | null;
      constructor(type: string, init: { alpha?: number } = {}) {
        super(type);
        this.alpha = init.alpha ?? null;
      }
    }
    Object.defineProperty(window, "DeviceOrientationEvent", { value: FakeDeviceOrientationEvent, configurable: true });
    Object.defineProperty(window.screen, "orientation", {
      value: { angle: 0, addEventListener() {}, removeEventListener() {} },
      configurable: true,
    });
  });

  await page.goto("/");
  await expect(page.getByRole("group", { name: "Tracking dials" })).toBeVisible();
  await page.evaluate(() => {
    const EventCtor = (window as unknown as { DeviceOrientationEvent: new (type: string, init?: { alpha?: number }) => Event }).DeviceOrientationEvent;
    window.dispatchEvent(new EventCtor("deviceorientation", { alpha: 270 }));
  });
  await expect(page.getByText("Phone 90°", { exact: true })).toBeVisible();
  await expect(page.getByRole("group", { name: "Tracking dials" })).toHaveCSS("flex-direction", "row");
  await expect(page.getByText(/aircraft · sorted by score · tap to track/i)).toHaveCount(0);
});
