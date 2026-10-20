import { test, expect } from "@playwright/test";
import { isValidLocator, locatorToLatlon } from "../src/lib/geo";

test("distance calculations use valid 4, 6, and 8 character grid centers", () => {
  expect(locatorToLatlon("CN87")).toEqual([47.5, -123]);
  const six = locatorToLatlon(" cn87vp ");
  expect(six[0]).toBeCloseTo(47 + 15.5 / 24, 10);
  expect(six[1]).toBeCloseTo(-124 + 21.5 * 2 / 24, 10);
  const eight = locatorToLatlon("CN87VP00");
  expect(eight[0]).toBeCloseTo(47 + 15 / 24 + 0.5 / 240, 10);
  expect(eight[1]).toBeCloseTo(-124 + 21 * 2 / 24 + 0.5 * 2 / 240, 10);
  for (const grid of [null, "", "ZZ99", "CN87V", "CN87VPXX"]) {
    expect(isValidLocator(grid)).toBe(false);
  }
  expect(() => locatorToLatlon("ZZ99")).toThrow("Invalid locator");
});

test("station distance follows active MY and DX grids and handles unknown grids", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.portableGridOverrides", JSON.stringify({ VE7NX: "CN89" }));
  });
  await page.goto("/");
  const distance = page.locator(".station-distance");
  await expect(distance).toHaveText("MY–DX distance: -- km");
  await page.getByRole("textbox", { name: "MY callsign", exact: true }).fill("K7MDL");
  await page.getByRole("textbox", { name: "MY grid", exact: true }).fill("CN87");
  await page.getByRole("button", { name: "Apply MY station" }).click();
  await page.getByRole("combobox", { name: "DX favorites" }).selectOption({ index: 1 });
  await expect(distance).toHaveText("MY–DX distance: 222.4 km");
  await page.getByRole("textbox", { name: "MY grid", exact: true }).fill("CN89");
  await page.getByRole("button", { name: "Apply MY station" }).click();
  await expect(distance).toHaveText("MY–DX distance: 0.0 km");
  await page.getByRole("button", { name: "MY station source", exact: true }).click();
  await expect(distance).toHaveText("MY–DX distance: -- km");
});

for (const { source, interval } of [
  { source: "opensky", interval: 30 },
  { source: "opensky", interval: 10 },
  { source: "adsbexchange", interval: 60 },
  { source: "dump1090", interval: 5 },
]) {
test(`locked tile becomes stale only beyond ${source} ${interval}s poll plus 5s`, async ({ page }) => {
  await page.clock.install({ time: new Date("2026-01-01T00:00:00Z") });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Rig", exact: true })).toBeVisible();
  await page.clock.pauseAt(new Date("2026-01-01T00:01:00Z"));
  await expect(page.getByText("opensky", { exact: true })).toBeVisible();
  await page.evaluate(({ source, interval }) => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "airscout_source_status", data: {
        active_source: source, priority: [source],
        sources: { [source]: { state: "connected", message: null } },
        poll_intervals_s: { [source]: interval },
      },
    }));
  }, { source, interval });
  const sendTracking = async (event: string, age: number, id = "abc123") => {
    await page.evaluate(({ event, age, id }) => {
      const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
      emit("sidecar-message", JSON.stringify({
        event, data: {
          icao24: id, callsign: event === "plane_locked" ? "INITIAL" : "UPDATED",
          altitude_ft: event === "plane_locked" ? 30000 : 35000,
          range_a_km: event === "plane_locked" ? 100 : 125.6,
          az_a: 123, el_a: 5, az_b: event === "plane_locked" ? 180 : 234,
          el_b: event === "plane_locked" ? 3 : 6, scatter_score: 0.8,
          off_path_deg: event === "plane_locked" ? 20 : 12, state: "tracking",
          position_updated_epoch_s: Math.floor(Date.now() / 1000) - age,
        },
      }));
    }, { event, age, id });
  };
  await sendTracking("plane_locked", 0);
  await expect(page.locator(".locked-plane")).toContainText("Distance from MY: 100.0 km");
  const age = page.locator(".locked-update-age");
  await page.clock.runFor((interval + 5) * 1000 + 999);
  await expect(age).toContainText(`${interval + 5}s ago`);
  await expect(age).not.toContainText("stale");
  await page.clock.runFor(1000);
  await expect(age).toContainText(`${interval + 6}s ago — stale`);
  await sendTracking("plane_tracking", 0, "another-plane");
  await expect(page.locator(".locked-plane")).toContainText("Distance from MY: 100.0 km");
  await expect(age).toContainText(`${interval + 6}s ago — stale`);
  await page.evaluate(({ source, interval }) => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "airscout_source_status", data: {
        active_source: source, priority: [source],
        sources: { [source]: { state: "connected", message: null } },
        poll_intervals_s: { [source]: interval + 10 },
      },
    }));
  }, { source, interval });
  await expect(age).not.toContainText("stale");
  await page.evaluate(() => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "airscout_source_status", data: {
        active_source: "dump1090", priority: ["dump1090"],
        sources: { dump1090: { state: "connected", message: null } },
        poll_intervals_s: { dump1090: 5 },
      },
    }));
  });
  await expect(age).toContainText("stale");
  await sendTracking("plane_tracking", 0);
  await expect(age).toContainText("0s ago");
  await expect(age).not.toContainText("stale");
  const tile = page.locator(".locked-plane");
  await expect(tile).toContainText("UPDATED");
  await expect(tile).toContainText("35,000 ft");
  await expect(tile).toContainText("Distance from MY: 125.6 km");
  await expect(tile).toContainText("234.0°");
  await expect(tile).toContainText("6.0°");
  await expect(tile).toContainText("12° off-path");
});
}

test("candidate text stays within the outline at the minimum window width", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 720 });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Rig", exact: true })).toBeVisible();
  await page.evaluate(() => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "airscout_update",
      data: {
        total: 1, station_a: "CN87", station_b: "CN89",
        aircraft: [{
          icao24: "abc123", callsign: "LONGCALLSIGN12345", altitude_ft: 45000,
          scatter_score: 1, az_a: 359, el_a: 90, az_b: 180, el_b: 10,
          off_path_deg: 180,
        }],
      },
    }));
    emit("sidecar-message", JSON.stringify({
      event: "plane_locked",
      data: {
        icao24: "abc123", callsign: "LONGCALLSIGN12345", altitude_ft: 45000,
        scatter_score: 1, az_a: 359, el_a: 90, az_b: 180, el_b: 10,
        off_path_deg: 180, state: "plane_locked", range_a_km: 125.6,
        rotator_az: 0, rotator_el: 0,
      },
    }));
  });
  const row = page.locator(".plane-candidate");
  await expect(row).toBeVisible();
  const contained = await row.evaluate(element => {
    const bounds = element.getBoundingClientRect();
    return element.scrollWidth <= element.clientWidth
      && [...element.children].every(child => {
        const rect = child.getBoundingClientRect();
        return rect.left >= bounds.left && rect.right <= bounds.right
          && rect.top >= bounds.top && rect.bottom <= bounds.bottom;
      });
  });
  expect(contained).toBe(true);
  await expect(row).toContainText("45,000 ft");
  await expect(row).toContainText("AZ/EL 359°/90°");
  await expect(row.locator("[title='LONGCALLSIGN12345']")).toBeVisible();
  const locked = page.locator(".locked-plane");
  await expect(locked.locator("[title='LONGCALLSIGN12345']")).toHaveCSS("text-align", "center");
  const iconGap = await locked.locator("[title='LONGCALLSIGN12345']").evaluate(element => {
    const icon = element.closest(".locked-plane")!.querySelector("svg")!.getBoundingClientRect();
    return element.getBoundingClientRect().left - icon.right;
  });
  expect(iconGap).toBeGreaterThanOrEqual(12);
  await expect(locked).toContainText("Aircraft altitude: 45,000 ft");
  await expect(locked).toContainText("Distance from MY: 125.6 km");
  const altitude = locked.getByText(/Aircraft altitude:/);
  await expect(altitude).toBeVisible();
  const fits = await altitude.evaluate(element => {
    const tile = element.closest(".locked-plane")!.getBoundingClientRect();
    const text = element.getBoundingClientRect();
    return text.left >= tile.left && text.right <= tile.right;
  });
  expect(fits).toBe(true);
  const stationLayout = await locked.locator(".locked-stations").evaluate(element => {
    const [my, dx] = [...element.children].map(child => child.getBoundingClientRect());
    return { sameRow: Math.abs(my.top - dx.top) < 1, sideBySide: my.right <= dx.left };
  });
  expect(stationLayout).toEqual({ sameRow: true, sideBySide: true });
  expect(await locked.evaluate(element => element.getBoundingClientRect().height)).toBeLessThan(240);
  await page.setViewportSize({ width: 900, height: 1080 });
  const card = page.locator(".plane-lock-card");
  await expect(card).toBeVisible();
  const unusedSpace = await card.evaluate(element => {
    const content = element.firstElementChild!.getBoundingClientRect();
    return element.getBoundingClientRect().bottom - content.bottom;
  });
  expect(unusedSpace).toBeLessThanOrEqual(10);
});
