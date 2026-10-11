import { test, expect } from "@playwright/test";

for (const { source, interval } of [
  { source: "opensky", interval: 30 },
  { source: "opensky", interval: 10 },
  { source: "adsbexchange", interval: 60 },
  { source: "dump1090", interval: 5 },
]) {
test(`locked tile becomes stale only beyond ${source} ${interval}s poll plus 5s`, async ({ page }) => {
  await page.clock.install({ time: new Date("2026-01-01T00:00:00Z") });
  await page.goto("/");
  await expect(page.getByRole("combobox", { name: "Rig", exact: true })).toBeVisible();
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
          az_a: 123, el_a: 5, az_b: event === "plane_locked" ? 180 : 234,
          el_b: event === "plane_locked" ? 3 : 6, scatter_score: 0.8,
          off_path_deg: event === "plane_locked" ? 20 : 12, state: "tracking",
          position_updated_epoch_s: Math.floor(Date.now() / 1000) - age,
        },
      }));
    }, { event, age, id });
  };
  await sendTracking("plane_locked", 0);
  const age = page.locator(".locked-update-age");
  await page.clock.runFor((interval + 5) * 1000 + 999);
  await expect(age).toContainText(`${interval + 5}s ago`);
  await expect(age).not.toContainText("stale");
  await page.clock.runFor(1000);
  await expect(age).toContainText(`${interval + 6}s ago — stale`);
  await sendTracking("plane_tracking", 0, "another-plane");
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
  await expect(tile).toContainText("234.0°");
  await expect(tile).toContainText("6.0°");
  await expect(tile).toContainText("12° off-path");
});
}

test("candidate text stays within the outline at the minimum window width", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 720 });
  await page.goto("/");
  await expect(page.getByRole("combobox", { name: "Rig", exact: true })).toBeVisible();
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
        off_path_deg: 180, state: "plane_locked",
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
  await expect(locked).toContainText("Aircraft altitude: 45,000 ft");
  const altitude = locked.getByText(/Aircraft altitude:/);
  await expect(altitude).toBeVisible();
  const fits = await altitude.evaluate(element => {
    const tile = element.closest(".locked-plane")!.getBoundingClientRect();
    const text = element.getBoundingClientRect();
    return text.left >= tile.left && text.right <= tile.right;
  });
  expect(fits).toBe(true);
});
