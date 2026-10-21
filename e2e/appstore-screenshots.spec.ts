import { test, expect } from "@playwright/test";

async function emitSidecar(
  page: import("@playwright/test").Page,
  event: string,
  data: Record<string, unknown>,
) {
  await page.evaluate(
    ({ event, data }) => {
      const emit = (window as unknown as Record<string, (e: string, p: string) => void>).__tauriMockEmit;
      if (emit) emit("sidecar-message", JSON.stringify({ event, data }));
    },
    { event, data },
  );
}

const mockAircraft = [
  {
    icao24: "a1b2c3", callsign: "UAL1547", lat: 47.68, lon: -122.25,
    altitude_ft: 38100, velocity_kts: 465, heading_deg: 172,
    az_a: 145.2, el_a: 4.8, range_a_km: 28, az_b: 198.4, el_b: 5.1,
    range_b_km: 31, off_path_deg: 8.2, scatter_score: 0.82,
  },
  {
    icao24: "d4e5f6", callsign: "ASA482", lat: 47.72, lon: -122.18,
    altitude_ft: 35000, velocity_kts: 440, heading_deg: 355,
    az_a: 32.6, el_a: 3.2, range_a_km: 45, az_b: 15.8, el_b: 2.8,
    range_b_km: 52, off_path_deg: 14.5, scatter_score: 0.64,
  },
  {
    icao24: "789abc", callsign: "DAL2291", lat: 47.55, lon: -122.35,
    altitude_ft: 41000, velocity_kts: 490, heading_deg: 210,
    az_a: 215.3, el_a: 6.1, range_a_km: 22, az_b: 242.7, el_b: 5.5,
    range_b_km: 26, off_path_deg: 22.3, scatter_score: 0.45,
  },
  {
    icao24: "def012", callsign: "SWA3318", lat: 47.82, lon: -122.40,
    altitude_ft: 32000, velocity_kts: 420, heading_deg: 5,
    az_a: 338.1, el_a: 2.4, range_a_km: 62, az_b: 320.5, el_b: 1.9,
    range_b_km: 68, off_path_deg: 35.1, scatter_score: 0.28,
  },
];

const mockDecodes = [
  { message: "CQ K7MDL CN87", snr: -18, delta_freq_hz: 1247, mode: "Q65", time_ms: 43200000 },
  { message: "AD7NP K7MDL R-19", snr: -19, delta_freq_hz: 1250, mode: "Q65", time_ms: 43230000 },
  { message: "K7MDL AD7NP RR73", snr: -16, delta_freq_hz: 1247, mode: "Q65", time_ms: 43260000 },
  { message: "K7MDL AD7NP 73", snr: -15, delta_freq_hz: 1247, mode: "Q65", time_ms: 43290000 },
];

async function setupDesktopScene(page: import("@playwright/test").Page, opts: { transmitting: boolean; qsoState: string }) {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.portableViewMode", "hidden");
    (window as unknown as Record<string, unknown>).__mockGrids = { K7MDL: "CN87tp" };
  });

  page.on("dialog", async (dialog) => {
    if (dialog.type() === "prompt") await dialog.accept("K7MDL");
  });

  await page.goto("/");
  await page.waitForTimeout(1200);

  await emitSidecar(page, "wsjtx_connected", { app_id: "WSJT-X v2.7.1" });
  await emitSidecar(page, "wsjtx_status", {
    freq_mhz: 10368.1, mode: "Q65", sub_mode: "C",
    de_call: "AD7NP", de_grid: "CN87vp", dx_call: "K7MDL",
    transmitting: opts.transmitting, decoding: !opts.transmitting,
    tr_period: 15, rx_df: 1247, tx_df: 1250, config_name: "10 GHz",
  });

  await page.getByRole("button", { name: "+ Other" }).click();
  await page.waitForTimeout(500);

  await emitSidecar(page, "rotator_connected", { host: "localhost", port: 4533 });
  await emitSidecar(page, "rotator_position", { azimuth: 145.2, elevation: 4.8 });

  await emitSidecar(page, "airscout_update", {
    aircraft: mockAircraft, total: 47,
    station_a: "CN87vp", station_b: "CN87tp",
    active_source: "opensky",
    last_update_epoch_s: Math.floor(Date.now() / 1000) - 3,
    cache_age_s: 3, data_freshness: "fresh",
  });

  await emitSidecar(page, "airscout_source_status", {
    active_source: "opensky", priority: ["opensky", "dump1090"],
    sources: {
      opensky: { state: "connected", message: null },
      dump1090: { state: "stopped", message: null },
    },
  });

  await emitSidecar(page, "plane_locked", {
    icao24: "a1b2c3", callsign: "UAL1547", altitude_ft: 38100,
    az_a: 145.2, el_a: 4.8, az_b: 198.4, el_b: 5.1,
    scatter_score: 0.82, off_path_deg: 8.2,
    state: opts.qsoState, rotator_az: 145.2, rotator_el: 4.8,
  });

  for (const decode of mockDecodes) {
    await emitSidecar(page, "wsjtx_decode", decode);
    await page.waitForTimeout(100);
  }

  await page.waitForTimeout(500);
}

test.describe("App Store Screenshots — Mac Retina 16\"", () => {
  test.use({ viewport: { width: 2880, height: 1800 }, deviceScaleFactor: 1 });

  test("1 — Active contact with aircraft locked", async ({ page }) => {
    await setupDesktopScene(page, { transmitting: false, qsoState: "tracking" });
    await page.screenshot({
      path: "appstore-assets/screenshots/mac/01-active-contact-2880x1800.png",
      fullPage: false,
    });
    await expect(page.getByRole("banner").getByText("AD7NP")).toBeVisible();
    await expect(page.getByText("10368.100 MHz")).toBeVisible();
  });

  test("2 — QSO in progress with TX", async ({ page }) => {
    await setupDesktopScene(page, { transmitting: true, qsoState: "active_qso" });
    await page.screenshot({
      path: "appstore-assets/screenshots/mac/02-qso-in-progress-2880x1800.png",
      fullPage: false,
    });
  });
});

test.describe("App Store Screenshots — Mac Retina 15\"", () => {
  test.use({ viewport: { width: 2560, height: 1600 }, deviceScaleFactor: 1 });

  test("1 — Active contact", async ({ page }) => {
    await setupDesktopScene(page, { transmitting: false, qsoState: "tracking" });
    await page.screenshot({
      path: "appstore-assets/screenshots/mac/01-active-contact-2560x1600.png",
      fullPage: false,
    });
  });

  test("2 — QSO in progress", async ({ page }) => {
    await setupDesktopScene(page, { transmitting: true, qsoState: "active_qso" });
    await page.screenshot({
      path: "appstore-assets/screenshots/mac/02-qso-in-progress-2560x1600.png",
      fullPage: false,
    });
  });
});
