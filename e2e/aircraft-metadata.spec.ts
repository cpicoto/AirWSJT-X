import { execFileSync } from "node:child_process";
import { test, expect } from "@playwright/test";
import { aircraftBadges, aircraftEmergency, aircraftIconClass, aircraftOpacity, networkMetadata,
  normalizeCategory, scatterEligible, type AircraftMetadata } from "../src/lib/aircraftMetadata";
import { applyScatterPreferences, compareRanking, type AircraftRanking, type RankingMethod } from "../src/lib/aircraftRanking";
import { predictUsableTime } from "../src/lib/usableTime";
import { scatterGeometry } from "../src/lib/geo";

test("metadata scoring agrees between Python and TS for all methods, boundaries and unknown data", () => {
  const cases: { metadata: AircraftMetadata; rank: AircraftRanking; now: number; method: RankingMethod;
    enabled: boolean; expected: AircraftRanking }[] = JSON.parse(execFileSync("python", ["-c",
    "import json; from tests.test_aircraft_metadata import preference_parity_cases; print(json.dumps(preference_parity_cases()))",
  ], { encoding: "utf8" }));
  for (const item of cases) {
    const actual = applyScatterPreferences(item.rank, item.metadata, item.now, item.method, item.enabled);
    for (const key of ["ranking_value", "range_gain_db", "ranking_window_s", "scatter_adjustment_db"] as const) {
      if (item.expected[key] == null) expect(actual[key]).toBe(item.expected[key]);
      else expect(actual[key]).toBeCloseTo(item.expected[key]!, 8);
    }
    expect(actual.scatter_eligible).toBe(item.expected.scatter_eligible);
    expect(actual.ranking_reason).toBe(item.expected.ranking_reason);
  }
});

test("icon categories, widebody refinement, flags, emergencies and age are independent", () => {
  const categories = {
    A1: "light", A2: "small-jet", A3: "medium-jet", A4: "medium-jet", A5: "heavy",
    A6: "fast-jet", A7: "helicopter", B1: "glider", B2: "balloon", B4: "light", B6: "drone", C1: "ground",
  };
  for (const [category, shape] of Object.entries(categories)) {
    expect(aircraftIconClass({ category })).toBe(shape);
  }
  for (const category of [undefined, null, "A0", "B0", "C0", "D1", "A8"]) {
    expect(normalizeCategory(category)).toBeNull();
    expect(aircraftIconClass({ category })).toBe("generic");
    expect(scatterEligible({ category })).toBe(true);
  }
  expect(aircraftIconClass({ type_code: "B77W" })).toBe("heavy");
  expect(aircraftIconClass({ category: "A7", type_code: "B77W" })).toBe("helicopter");
  expect(aircraftIconClass({ category: "A5", on_ground: true })).toBe("ground");
  expect(aircraftBadges({ db_flags: 15 })).toEqual(["MIL", "INT", "PIA", "LADD"]);
  for (const squawk of ["7500", "7600", "7700"]) expect(aircraftEmergency({ squawk })).toBe(true);
  expect(aircraftEmergency({ emergency: "general" })).toBe(true);
  expect(aircraftEmergency({ emergency: "none", squawk: "1200" })).toBe(false);
  expect(aircraftOpacity({}, 1000)).toBe(1);
  expect(aircraftOpacity({ position_updated_epoch_s: 1001 }, 1000)).toBe(1);
  expect(aircraftOpacity({ position_updated_epoch_s: 1000 }, 1000)).toBe(1);
  expect(aircraftOpacity({ position_updated_epoch_s: 1000 }, 1060)).toBeCloseTo(0.7);
  expect(aircraftOpacity({ position_updated_epoch_s: 1000 }, 1200)).toBe(0.35);
  expect(aircraftOpacity({ last_seen_epoch_s: 1000 }, 1060)).toBeCloseTo(0.7);
  expect(networkMetadata({ category: " a5 ", t: "B77W", dbFlags: 1 }).category).toBe("A5");
  expect(() => networkMetadata({ dbFlags: -1 })).toThrow();
  expect(() => networkMetadata({ t: 123 })).toThrow();
});

test("mutual geometry eligibility is computed before display elevation rounding", () => {
  const above = scatterGeometry(0, 0, 0, 0, 0, 1, 750);
  expect(above.el_a).toBe(0);
  expect(above.qso_viable).toBe(true);
  expect(scatterGeometry(0, 0, 0, 0, 0, 1, 700).qso_viable).toBe(false);
});

test("desktop scatter preference toggle is persisted and sent as a boolean", async ({ page }) => {
  await page.goto("/");
  await page.locator(".aircraft-filters summary").click();
  const toggle = page.getByRole("checkbox", { name: "Aircraft scatter preferences" });
  await expect(toggle).toBeChecked();
  await toggle.uncheck();
  await expect.poll(() => page.evaluate(() => {
    const requests = Reflect.get(window, "__mockRpcRequests") as { method: string; params: { filters?: { scatter_metadata?: boolean } } }[];
    return requests.filter(request => request.method === "airscout_apply_filters").at(-1)?.params.filters?.scatter_metadata;
  })).toBe(false);
  await expect.poll(() => page.evaluate(() =>
    JSON.parse(localStorage.getItem("airwsjtx.aircraftFilters.DEFAULT__DEFAULT") ?? "{}").scatter_metadata
  )).toBe(false);
  await page.reload();
  await page.locator(".aircraft-filters summary").click();
  await expect(toggle).not.toBeChecked();
});

test("unknown metadata is neutral and surface targets fail shared window eligibility", () => {
  const rank: AircraftRanking = { ranking_value: 0.5, range_gain_db: null, ranking_reason: "Legacy geometry score",
    ranking_window_s: null, ranking_window_capped: false };
  const unknown = applyScatterPreferences(rank, {}, 1000, "legacy");
  expect(unknown.ranking_value).toBe(rank.ranking_value);
  const heavy = applyScatterPreferences(rank, { category: "A5" }, 1000, "legacy");
  expect(compareRanking({ ...heavy, scatter_score: 0.5 }, { ...unknown, scatter_score: 0.5 }, "legacy")).toBeLessThan(0);
  const disabled = applyScatterPreferences(rank, { category: "A5" }, 1000, "legacy", false);
  expect(disabled.ranking_value).toBe(rank.ranking_value);
  const aircraft = { lat: 0, lon: 1, altitude_ft: 33000, heading_deg: 90, velocity_kts: 400, category: "C1" };
  expect(predictUsableTime(aircraft, { lat: 0, lon: 0, altitudeM: 0 }, { lat: 0, lon: 2, altitudeM: 0 }).seconds).toBe(0);
});

test("Sky Dome renders heading-oriented silhouettes, emergency and vertical overlays with rich tooltips", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("airwsjtx.showSkyDome", "true"));
  await page.goto("/");
  await expect(page.locator(".skydome-container")).toBeVisible();
  await page.evaluate(() => {
    const emit = Reflect.get(window, "__tauriMockEmit");
    emit("sidecar-message", JSON.stringify({ event: "airscout_update", data: {
      aircraft: [{
        icao24: "abc123", callsign: "HEAVY1", lat: 47, lon: -122, altitude_ft: 35000,
        velocity_kts: 450, heading_deg: 135, az_a: 90, el_a: 25, az_b: 270, el_b: 25,
        range_a_km: 100, range_b_km: 100, off_path_deg: 0, scatter_score: 0.7,
        category: "A5", type_code: "B77W", registration: "N123", description: "Boeing 777",
        vertical_rate_ms: -5, squawk: "7700", db_flags: 15, position_source: "adsbexchange",
        position_updated_epoch_s: Date.now() / 1000 - 60,
      }], total: 1, station_a: "CN87", station_b: "CN89",
    } }));
  });
  const marker = page.locator('.skydome-container [data-aircraft-id="abc123"]');
  await expect(marker.locator('[data-aircraft-icon="heavy"] > path').first()).toHaveAttribute("transform", "rotate(135)");
  await expect(marker.locator('[data-aircraft-icon="heavy"]')).toHaveAttribute("width", "32");
  await expect(marker.locator('[data-aircraft-heading="abc123"]')).toHaveAttribute("transform", /rotate\(135\)/);
  await expect(marker.locator('[data-aircraft-icon="heavy"] > path').first()).toHaveAttribute("stroke", "var(--red)");
  await expect(marker.locator('[data-vertical-direction="descent"]')).toHaveCount(1);
  await expect(marker.locator("title")).toContainText("B77W · N123 · Boeing 777");
  await expect(marker.locator("title")).toContainText("MIL · INT · PIA · LADD · ADSBExchange");
  expect(Number(await marker.getAttribute("opacity"))).toBeLessThan(0.8);
  await marker.getByRole("button", { name: "Lock HEAVY1" }).click();
  await expect.poll(() => page.evaluate(() => Reflect.get(window, "__mockLockRequest"))).toBe("abc123");
  await page.locator(".skydome-container").screenshot({ path: test.info().outputPath("aircraft-category-icons.png") });
  await page.evaluate(() => {
    window.__tauriMockEmit("sidecar-message", JSON.stringify({ event: "airscout_update", data: {
      aircraft: [{ icao24: "unknown", callsign: "UNKNOWN", lat: 47, lon: -122, altitude_ft: 35000,
        velocity_kts: null, heading_deg: null, az_a: 90, el_a: 25, range_a_km: 100,
        off_path_deg: 0, scatter_score: 0.5 }],
      total: 1, station_a: "CN87", station_b: "CN89",
    } }));
  });
  await expect(page.locator('.skydome-container [data-aircraft-icon="generic"]')).toHaveCount(1);
  await expect(page.locator(".skydome-container [data-aircraft-heading]")).toHaveCount(0);
});

test.describe("mobile metadata selection", () => {
  test.use({ userAgent: "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36", viewport: { width: 390, height: 844 } });
  test("network metadata reaches icons and selection, and disabling preferences restores geometry order", async ({ page }) => {
    await page.addInitScript(() => {
      if (!localStorage.getItem("airwsjtx.mobile.settings")) {
        localStorage.setItem("airwsjtx.mobile.settings", JSON.stringify({
          myCall: "TEST", myGrid: "JJ00", dxCall: "DX", dxGrid: "JJ10", speechEnabled: false,
          aircraftSource: "adsbexchange",
        }));
      }
      const ac = (hex: string, category: string) => ({
        hex, category, flight: hex, lat: 0.5, lon: 2, alt_geom: 35000, gs: 400, track: 90,
        geom_rate: 0, seen_pos: 0, t: "B77W", r: "N123", desc: "Test aircraft", dbFlags: 1,
      });
      Object.assign(window, { __mockMobileResponse: {
        now: Date.now(), ac: [ac("a-light", "A1"), ac("z-heavy", "A5"), ac("surface", "C1")],
      } });
    });
    await page.goto("/");
    const list = page.getByRole("list", { name: "Aircraft list" });
    await expect(list.getByRole("listitem")).toHaveCount(2);
    await expect(list.getByRole("listitem").first()).toContainText("z-heavy");
    await expect(list.getByRole("listitem").first().locator('[data-aircraft-icon="heavy"]')).toHaveCount(1);
    await expect(list).toContainText("ADSBExchange");
    await expect(list).toContainText("MIL");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("checkbox", { name: "Aircraft scatter preferences" }).uncheck();
    await page.getByRole("button", { name: "Done", exact: true }).click();
    await expect(list.getByRole("listitem").first()).toContainText("a-light");
    await page.reload();
    await expect(list.getByRole("listitem").first()).toContainText("a-light");
  });
});
