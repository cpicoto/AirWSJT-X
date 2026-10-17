import { test, expect } from "@playwright/test";

// OpenSky state vectors: [icao24, callsign, country, time_pos, last_contact, lon, lat,
// baro_alt_m, on_ground, velocity_ms, true_track, vert_rate, sensors, geo_alt_m]
const states = [
  ["a1b2c3", "UAL1547", "US", 1, 1, -122.55, 47.95, 11600, false, 239, 172, 0, null, 11600],
  ["d4e5f6", "ASA482", "US", 1, 1, -122.05, 47.85, 10700, false, 226, 355, 0, null, 10700],
  ["789abc", "DAL2291", "US", 1, 1, -123.30, 47.25, 12500, false, 252, 210, 0, null, 12500],
  ["def012", "SWA3318", "US", 1, 1, -123.65, 47.75, 9750, false, 216, 5, 0, null, 9750],
  ["345678", "FDX1180", "US", 1, 1, -122.30, 47.20, 11000, false, 245, 90, 0, null, 11000],
  ["9abcde", "AAL902", "US", 1, 1, -123.90, 47.40, 11900, false, 248, 135, 0, null, 11900],
];

async function setup(page: import("@playwright/test").Page, path: string, callsign = "UAL1547") {
  await page.addInitScript((mockStates) => {
    localStorage.setItem("airwsjtx.mobile.settings", JSON.stringify({
      myCall: "AD7NP", myGrid: "CN87", dxCall: "K7MDL", dxGrid: "CN87tp", speechEnabled: true,
    }));
    (window as unknown as Record<string, unknown>).__mockMobileStates = mockStates;
  }, states);
  await page.goto("/");
  await expect(page.getByText(/aircraft · sorted by score/i)).toBeVisible({ timeout: 15000 });
  await page.getByText(callsign).last().click();
  await page.waitForTimeout(800);
  await page.screenshot({ path, fullPage: false });
}

const devices = [
  { dir: "iphone", label: "iPhone 6.9\"", width: 440, height: 956, scale: 3, size: "1320x2868" },
  { dir: "ipad", label: "iPad 13\"", width: 1024, height: 1366, scale: 2, size: "2048x2732" },
];

for (const d of devices) {
  test.describe(`App Store Screenshots — ${d.label}`, () => {
    test.use({
      userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15",
      viewport: { width: d.width, height: d.height },
      deviceScaleFactor: d.scale,
    });

    test("1 — Tracking an aircraft", async ({ page }) => {
      await setup(page, `appstore-assets/screenshots/${d.dir}/01-tracking-${d.size}.png`);
    });

    test("2 — Tracking a second aircraft", async ({ page }) => {
      await setup(page, `appstore-assets/screenshots/${d.dir}/02-tracking-${d.size}.png`, "FDX1180");
    });
  });
}
