import { test } from "@playwright/test";

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

async function setState(page: import("@playwright/test").Page, state: string) {
  await page.evaluate((s) => {
    (window as unknown as Record<string, string>).__mockState = s;
  }, state);
}

const aircraft = [
  {
    icao24: "a1b2c3", callsign: "UAL1547", lat: 47.68, lon: -122.25,
    altitude_ft: 38100, velocity_kts: 465, heading_deg: 172,
    az_a: 145.2, el_a: 4.8, range_a_km: 28,
    az_b: 198.4, el_b: 5.1, range_b_km: 31,
    off_path_deg: 8.2, scatter_score: 0.82,
  },
  {
    icao24: "d4e5f6", callsign: "ASA482", lat: 47.72, lon: -122.18,
    altitude_ft: 35000, velocity_kts: 440, heading_deg: 355,
    az_a: 32.6, el_a: 3.2, range_a_km: 45,
    az_b: 15.8, el_b: 2.8, range_b_km: 52,
    off_path_deg: 14.5, scatter_score: 0.64,
  },
  {
    icao24: "789abc", callsign: "DAL2291", lat: 47.55, lon: -122.35,
    altitude_ft: 41000, velocity_kts: 490, heading_deg: 210,
    az_a: 215.3, el_a: 6.1, range_a_km: 22,
    az_b: 242.7, el_b: 5.5, range_b_km: 26,
    off_path_deg: 22.3, scatter_score: 0.45,
  },
  {
    icao24: "def012", callsign: "SWA3318", lat: 47.82, lon: -122.40,
    altitude_ft: 32000, velocity_kts: 420, heading_deg: 5,
    az_a: 338.1, el_a: 2.4, range_a_km: 62,
    az_b: 320.5, el_b: 1.9, range_b_km: 68,
    off_path_deg: 35.1, scatter_score: 0.28,
  },
];

test.describe("Demo Video", () => {
  test("full contact lifecycle — AD7NP ↔ K7MDL on 10 GHz", async ({ page }) => {
    test.setTimeout(120000);

    await page.addInitScript(() => {
      localStorage.setItem("airwsjtx.portableViewMode", "hidden");
      (window as unknown as Record<string, unknown>).__mockGrids = { K7MDL: "CN87tp" };
      (window as unknown as Record<string, unknown>).__mockState = "idle";
    });

    page.on("dialog", async (dialog) => {
      if (dialog.type() === "prompt") await dialog.accept("K7MDL");
    });

    // ── Phase 1: App startup — idle ──────────────────────────────────
    await page.goto("/");
    await page.waitForTimeout(3000);

    // ── Phase 2: WSJT-X connects ────────────────────────────────────
    await emitSidecar(page, "wsjtx_connected", { app_id: "WSJT-X v2.7.1" });
    await page.waitForTimeout(1500);

    await emitSidecar(page, "wsjtx_status", {
      freq_mhz: 10368.1, mode: "Q65", sub_mode: "C",
      de_call: "AD7NP", de_grid: "CN87vp", dx_call: "",
      transmitting: false, decoding: true,
      tr_period: 15, rx_df: 1247, tx_df: 1250,
      config_name: "10 GHz",
    });
    await page.waitForTimeout(2000);

    // ── Phase 3: Set DX station K7MDL ───────────────────────────────
    await setState(page, "stations_ready");
    await page.getByRole("button", { name: "+ Other" }).click();
    await page.waitForTimeout(2000);

    // ── Phase 4: Rotator connects ───────────────────────────────────
    await emitSidecar(page, "rotator_connected", { host: "localhost", port: 4533 });
    await emitSidecar(page, "rotator_position", { azimuth: 0, elevation: 0 });
    await page.waitForTimeout(1500);

    // ── Phase 5: AirScout source comes online ───────────────────────
    await emitSidecar(page, "airscout_source_status", {
      active_source: "opensky",
      priority: ["opensky", "dump1090"],
      sources: {
        opensky: { state: "connected", message: null },
        dump1090: { state: "stopped", message: null },
      },
    });
    await page.waitForTimeout(1000);

    // ── Phase 6: Aircraft start appearing ───────────────────────────
    await setState(page, "candidates_available");

    // First just 1 aircraft
    await emitSidecar(page, "airscout_update", {
      aircraft: [aircraft[3]],
      flight_data: [aircraft[3]],
      total: 12, station_a: "CN87vp", station_b: "CN87tp",
      active_source: "opensky",
      last_update_epoch_s: Math.floor(Date.now() / 1000),
      cache_age_s: 1, data_freshness: "fresh",
    });
    await page.waitForTimeout(1500);

    // Then 2 aircraft
    await emitSidecar(page, "airscout_update", {
      aircraft: [aircraft[2], aircraft[3]],
      flight_data: [aircraft[2], aircraft[3]],
      total: 23, station_a: "CN87vp", station_b: "CN87tp",
      active_source: "opensky",
      last_update_epoch_s: Math.floor(Date.now() / 1000),
      cache_age_s: 1, data_freshness: "fresh",
    });
    await page.waitForTimeout(1500);

    // Then all 4 aircraft
    await emitSidecar(page, "airscout_update", {
      aircraft,
      flight_data: aircraft,
      total: 47, station_a: "CN87vp", station_b: "CN87tp",
      active_source: "opensky",
      last_update_epoch_s: Math.floor(Date.now() / 1000),
      cache_age_s: 1, data_freshness: "fresh",
    });
    await page.waitForTimeout(2000);

    // ── Phase 7: Lock on best candidate — UAL1547 ───────────────────
    await setState(page, "tracking");
    await emitSidecar(page, "plane_locked", {
      icao24: "a1b2c3", callsign: "UAL1547", altitude_ft: 38100,
      az_a: 145.2, el_a: 4.8, az_b: 198.4, el_b: 5.1,
      scatter_score: 0.82, off_path_deg: 8.2,
      state: "tracking", rotator_az: 145.2, rotator_el: 4.8,
    });

    // Rotator slews to target
    const azSteps = [20, 45, 78, 110, 130, 140, 143, 145.2];
    const elSteps = [0.5, 1.2, 2.0, 2.8, 3.5, 4.0, 4.4, 4.8];
    for (let i = 0; i < azSteps.length; i++) {
      await emitSidecar(page, "rotator_position", {
        azimuth: azSteps[i], elevation: elSteps[i],
      });
      await page.waitForTimeout(300);
    }
    await page.waitForTimeout(1500);

    // ── Phase 8: Tracking updates — plane moves slightly ────────────
    for (let i = 0; i < 3; i++) {
      const azDrift = 145.2 + (i + 1) * 0.8;
      const elDrift = 4.8 + (i + 1) * 0.15;
      const scoreDrift = 0.82 - (i + 1) * 0.01;
      await emitSidecar(page, "plane_tracking", {
        icao24: "a1b2c3", callsign: "UAL1547",
        az_a: azDrift, el_a: elDrift,
        scatter_score: scoreDrift,
        state: "tracking",
        rotator_az: azDrift, rotator_el: elDrift,
      });
      await emitSidecar(page, "rotator_position", {
        azimuth: azDrift, elevation: elDrift,
      });
      await page.waitForTimeout(800);
    }

    // ── Phase 9: Q65-15s QSO exchange ─────────────────────────────────
    // Realistic Q65 timing: stations alternate TX/RX every 15 seconds.
    // K7MDL transmits on even sequences, AD7NP on odd.
    // AD7NP only sees decodes from K7MDL (not its own TX).
    // Timestamps increment by 15s per period.

    const wsjtxBase = {
      freq_mhz: 10368.1, mode: "Q65", sub_mode: "C",
      de_call: "AD7NP", de_grid: "CN87vp", dx_call: "K7MDL",
      tr_period: 15, rx_df: 1247, tx_df: 1250,
      config_name: "10 GHz",
    };

    // Build WSJT-X UTC milliseconds-since-midnight with proper 15s spacing.
    const qsoTimeMs = (seq: number) => 12 * 60 * 60 * 1000 + seq * 15000;

    // Seq 0 (even) — K7MDL CQ: AD7NP is receiving, decodes CQ
    await emitSidecar(page, "wsjtx_status", {
      ...wsjtxBase, transmitting: false, decoding: true,
    });
    await page.waitForTimeout(3000);
    await emitSidecar(page, "wsjtx_decode", {
      message: "CQ K7MDL CN87", snr: -18,
      delta_freq_hz: 1247, mode: "Q65", time_ms: qsoTimeMs(0),
    });
    await page.waitForTimeout(2000);

    // Seq 1 (odd) — AD7NP responds with grid: TX for 15s
    await setState(page, "active_qso");
    await emitSidecar(page, "wsjtx_status", {
      ...wsjtxBase, transmitting: true, decoding: false,
    });
    await page.waitForTimeout(5000);

    // Seq 2 (even) — K7MDL sends signal report: AD7NP receives & decodes
    await emitSidecar(page, "wsjtx_status", {
      ...wsjtxBase, transmitting: false, decoding: true,
    });
    await page.waitForTimeout(3000);
    await emitSidecar(page, "wsjtx_decode", {
      message: "AD7NP K7MDL R-19", snr: -19,
      delta_freq_hz: 1250, mode: "Q65", time_ms: qsoTimeMs(2),
    });
    await page.waitForTimeout(2000);

    // Seq 3 (odd) — AD7NP sends RR73: TX for 15s
    await emitSidecar(page, "wsjtx_status", {
      ...wsjtxBase, transmitting: true, decoding: false,
    });
    await page.waitForTimeout(5000);

    // Seq 4 (even) — K7MDL sends 73: AD7NP receives final confirmation
    await emitSidecar(page, "wsjtx_status", {
      ...wsjtxBase, transmitting: false, decoding: true,
    });
    await page.waitForTimeout(3000);
    await emitSidecar(page, "wsjtx_decode", {
      message: "K7MDL AD7NP 73", snr: -16,
      delta_freq_hz: 1247, mode: "Q65", time_ms: qsoTimeMs(4),
    });
    await page.waitForTimeout(2000);

    // ── Phase 10: QSO completed ─────────────────────────────────────
    await setState(page, "completed");
    await emitSidecar(page, "plane_locked", {
      icao24: "a1b2c3", callsign: "UAL1547", altitude_ft: 38100,
      az_a: 147.6, el_a: 5.25, az_b: 200.0, el_b: 5.4,
      scatter_score: 0.79, off_path_deg: 8.2,
      state: "completed", rotator_az: 147.6, rotator_el: 5.25,
    });
    await page.waitForTimeout(3000);

    // ── Phase 11: Unlock and return to idle ──────────────────────────
    await emitSidecar(page, "plane_unlocked", {});
    await setState(page, "idle");
    await emitSidecar(page, "rotator_position", { azimuth: 147.6, elevation: 5.25 });
    await page.waitForTimeout(2000);
  });
});
