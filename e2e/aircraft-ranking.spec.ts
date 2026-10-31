import { execFileSync } from "node:child_process";
import { test, expect } from "@playwright/test";
import { rankGeometry, compareRanking, formatRanking, type AircraftRanking, type RankingMethod } from "../src/lib/aircraftRanking";
import type { SkyCourseAircraft, SkyCourseStation } from "../src/lib/skyCourse";

test("Python and TypeScript ranking agree for geometry, symmetry, windows and missing data", () => {
  const cases: {
    aircraft: SkyCourseAircraft & { position_updated_epoch_s: number | null };
    a: SkyCourseStation; b: SkyCourseStation; method: RankingMethod; score: number; now: number;
    expected: AircraftRanking;
  }[] = JSON.parse(execFileSync("python", ["-c",
    "import json; from tests.test_aircraft_ranking import parity_cases; print(json.dumps(parity_cases()))",
  ], { encoding: "utf8" }));
  for (const item of cases) {
    const actual = rankGeometry(item.aircraft, item.a, item.b, item.method, item.score, item.now);
    for (const key of ["ranking_value", "range_gain_db", "ranking_window_s"] as const) {
      if (item.expected[key] === null) expect(actual[key]).toBeNull();
      else expect(actual[key]).toBeCloseTo(item.expected[key]!, 6);
    }
    expect(actual.ranking_window_capped).toBe(item.expected.ranking_window_capped);
    expect(actual.ranking_reason).toBe(item.expected.ranking_reason);
  }
});

test("range and window ranking have separate units and known windows precede fallback", () => {
  const base: AircraftRanking = {
    ranking_value: null, range_gain_db: 10, ranking_reason: "missing track",
    ranking_window_s: null, ranking_window_capped: false,
  };
  const unknown = { ...base, scatter_score: 0.9 };
  const known = { ...base, ranking_value: -2, scatter_score: 0.1 };
  expect(compareRanking(known, unknown, "window-product")).toBeLessThan(0);
  expect(compareRanking(known, unknown, "legacy")).toBeGreaterThan(0);
  expect(formatRanking(unknown, "window-product")).toBe("Range fallback");
  expect(formatRanking(known, "range-product")).toBe("-2.0 dB");
  expect(formatRanking(known, "window-product")).toBe("-2.0 index");
});

test("mobile adapters preserve position age rather than treating every fetch as fresh", async ({ page }) => {
  await page.goto("/");
  const timestamps = await page.evaluate(async () => {
    const path = "/src/lib/opensky.ts";
    const api = await import(path);
    Object.assign(window, { __mockMobileResponse: {
      states: [["abc123", "TEST", "US", 1000, 1005, 1, 0, 11000, false, 200, 90, 0, null, 11000]],
    } });
    const opensky = await api.fetchNativeAircraft("opensky", 0, 0, 1, 1);
    Object.assign(window, { __mockMobileResponse: {
      now: 1000000, ac: [{ hex: "abc123", lat: 0, lon: 1, alt_geom: 33000, gs: 400, track: 90, seen_pos: 50 }],
    } });
    const exchange = await api.fetchNativeAircraft("adsbexchange", 0, 0, 1, 1);
    Object.assign(window, { __mockMobileResponse: { ac: [{ hex: "abc123", seen_pos: 50 }] } });
    const unknown = await api.fetchNativeAircraft("adsbexchange", 0, 0, 1, 1);
    Object.assign(window, { __mockMobileResponse: {
      now: Date.now(), ac: [{ hex: "abc123", seen_pos: -5 }],
    } });
    const invalid = await api.fetchNativeAircraft("adsbexchange", 0, 0, 1, 1);
    Object.assign(window, { __mockDump1090PositionAge: 50 });
    const dump = await api.fetchDump1090Aircraft("localhost");
    const age = Date.now() / 1000 - dump[0].position_updated_epoch_s;
    return [opensky[0].position_updated_epoch_s, exchange[0].position_updated_epoch_s,
      unknown[0].position_updated_epoch_s, age, invalid[0].position_updated_epoch_s];
  });
  expect(timestamps[0]).toBe(1000);
  expect(timestamps[1]).toBe(950);
  expect(timestamps[2]).toBeNull();
  expect(timestamps[3]).toBeCloseTo(50, 1);
  expect(timestamps[4]).toBeNull();
});

test("rejected desktop filter persistence leaves the selector and saved policy unchanged", async ({ page }) => {
  await page.goto("/");
  const select = page.getByRole("button", { name: "Best-path ranking", exact: true });
  await expect(select).toHaveText("Legacy geometry (default)");
  await select.click();
  await page.getByRole("listbox").getByRole("option", { name: "Range product", exact: true }).click();
  await expect(select).toHaveText("Range product");
  await expect.poll(() => page.evaluate(() =>
    JSON.parse(localStorage.getItem("airwsjtx.aircraftFilters.DEFAULT__DEFAULT") ?? "{}").ranking_method
  )).toBe("range-product");
  const saved = await page.evaluate(() => localStorage.getItem("airwsjtx.aircraftFilters.DEFAULT__DEFAULT"));
  await page.evaluate(() => Object.assign(window, {
    __mockRpcErrors: { airscout_apply_filters: "disk full" },
  }));
  await select.click();
  await page.getByRole("listbox").getByRole("option", { name: "Window-weighted range product", exact: true }).click();
  await expect(page.getByRole("region", { name: "Log", exact: true })).toContainText("Aircraft filter update: Error: disk full");
  await expect(select).toHaveText("Range product");
  expect(await page.evaluate(() => localStorage.getItem("airwsjtx.aircraftFilters.DEFAULT__DEFAULT"))).toBe(saved);
});

test("desktop ranking selection is persisted and sent to backend; backend order is preserved", async ({ page }) => {
  await page.goto("/");
  const select = page.getByRole("button", { name: "Best-path ranking", exact: true });
  await expect(select).toHaveText("Legacy geometry (default)");
  await select.click();
  await page.getByRole("listbox").getByRole("option", { name: "Range product", exact: true }).click();
  await expect.poll(() => page.evaluate(() => {
    const requests = Reflect.get(window, "__mockRpcRequests") as { method: string; params: { filters?: { ranking_method?: string } } }[];
    return requests.filter(request => request.method === "airscout_apply_filters").at(-1)?.params.filters?.ranking_method;
  })).toBe("range-product");
  await page.reload();
  await expect(select).toHaveText("Range product");
  await page.evaluate(() => {
    const emit = Reflect.get(window, "__tauriMockEmit") as (event: string, payload: string) => void;
    const plane = (id: string, score: number) => ({
      icao24: id, callsign: id, lat: 0, lon: 1, altitude_ft: 33000,
      velocity_kts: 400, heading_deg: 90, az_a: 90, el_a: 5, az_b: 270, el_b: 5,
      range_a_km: 100, range_b_km: 200, off_path_deg: 0, scatter_score: score,
      ranking_value: 5, range_gain_db: 5, ranking_reason: "Range-only power; not SNR",
    });
    const aircraft = [plane("FIRST", 0.2), plane("SECOND", 0.8)];
    emit("sidecar-message", JSON.stringify({ event: "airscout_update", data: {
      aircraft, flight_data: aircraft, total: 2, station_a: null, station_b: null,
      ranking_method: "range-product",
    } }));
  });
  const rows = page.locator(".ac-table tbody tr");
  await expect(rows.first()).toContainText("FIRST");
  await expect(rows.first()).toContainText("+5.0 dB");
  await expect(rows.last()).toContainText("SECOND");
  await expect(page.locator(".plane-candidate").first()).toContainText("FIRST");
  await select.click();
  await page.getByRole("listbox").getByRole("option", { name: "Window-weighted range product", exact: true }).click();
  await expect(select).toHaveText("Window-weighted range product");
});

test.describe("mobile ranking", () => {
  test.use({ userAgent: "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36", viewport: { width: 390, height: 844 } });

  test("selector changes actual aircraft order, persists, and explains missing-window fallback", async ({ page }) => {
    await page.addInitScript(() => {
      if (!localStorage.getItem("airwsjtx.mobile.settings")) {
        localStorage.setItem("airwsjtx.mobile.settings", JSON.stringify({
          myCall: "TEST", myGrid: "JJ00", dxCall: "DX", dxGrid: "JJ10", speechEnabled: false,
        }));
      }
      const state = (id: string, lon: number, timestamp: number | null) =>
        [id, id, "US", timestamp, Date.now() / 1000, lon, 0.5, 11000, false, 200, 90, 0, null, 11000];
      Object.assign(window, { __mockMobileStates: [
        state("NEAR", 1.05, null), state("MID", 2, Date.now() / 1000),
      ] });
    });
    await page.goto("/");
    const list = page.getByRole("list", { name: "Aircraft list" });
    const first = list.getByRole("listitem").first();
    await expect(first).toContainText("MID");
    await first.click();
    const requestsBefore = await page.evaluate(() => Reflect.get(window, "__mockAircraftRequests").length);
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("combobox", { name: "Best-path ranking", exact: true }).selectOption("range-product");
    await page.getByRole("button", { name: "Done", exact: true }).click();
    await expect(first).toContainText("NEAR");
    await expect(first).toContainText("dB");
    expect(await page.evaluate(() => Reflect.get(window, "__mockAircraftRequests").length)).toBe(requestsBefore);
    await expect(page.getByRole("button", { name: "Auto", exact: true })).toBeVisible();
    await page.reload();
    await expect(first).toContainText("NEAR");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const select = page.getByRole("combobox", { name: "Best-path ranking", exact: true });
    await expect(select).toHaveValue("range-product");
    await select.selectOption("window-product");
    await page.getByRole("button", { name: "Done", exact: true }).click();
    await expect(first).toContainText("MID");
    await expect(list).toContainText("Range fallback");
    await expect(first).toContainText("index");
  });

  test("dump1090 ranking changes rerank the retained snapshot without restarting tracking", async ({ page }) => {
    await page.clock.install({ time: new Date("2030-01-01T00:00:00Z") });
    await page.clock.pauseAt(new Date("2030-01-01T00:01:00Z"));
    await page.addInitScript(() => {
      localStorage.setItem("airwsjtx.mobile.settings", JSON.stringify({
        myCall: "TEST", myGrid: "JJ00", dxCall: "DX", dxGrid: "JJ10", speechEnabled: false,
        aircraftSource: "dump1090", dump1090Host: "localhost",
      }));
      const plane = (id: string, lon: number) => ({
        icao24: id, callsign: id, lat: 0.5, lon, altitude_m: 11000,
        velocity_ms: 200, heading_deg: 90, vertical_rate_ms: 0, on_ground: false, position_age_s: 0,
      });
      Object.assign(window, { __mockDump1090Response: { aircraft: [plane("NEAR", 1.05), plane("MID", 2)] } });
    });
    await page.goto("/");
    const list = page.getByRole("list", { name: "Aircraft list" });
    await expect(list.getByRole("listitem").first()).toContainText("MID");
    await list.getByRole("listitem").first().click();
    const sourceBefore = await page.evaluate(() => ({
      requests: Reflect.get(window, "__mockDump1090Requests").length,
      stops: Reflect.get(window, "__mockDump1090Stops") ?? 0,
    }));
    await page.evaluate(() => Object.assign(window, { __mockDump1090Response: { aircraft: [] } }));
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("combobox", { name: "Best-path ranking", exact: true }).selectOption("range-product");
    await page.getByRole("button", { name: "Done", exact: true }).click();
    await expect(list.getByRole("listitem").first()).toContainText("NEAR");
    await expect(list).toContainText("dB");
    await expect(page.getByRole("button", { name: "Auto", exact: true })).toBeVisible();
    expect(await page.evaluate(() => ({
      requests: Reflect.get(window, "__mockDump1090Requests").length,
      stops: Reflect.get(window, "__mockDump1090Stops") ?? 0,
    }))).toEqual(sourceBefore);
  });
});
