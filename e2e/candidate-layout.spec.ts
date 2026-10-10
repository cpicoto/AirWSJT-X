import { test, expect } from "@playwright/test";

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
