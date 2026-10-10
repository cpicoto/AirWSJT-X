import { test, expect } from "@playwright/test";

test("discovers rigs, selects and persists a filter, and clears it", async ({ page }) => {
  await page.goto("/");
  const filter = page.getByRole("combobox", { name: "Rig", exact: true });
  await expect(filter).toHaveValue("");
  await page.evaluate(() => {
    (window as unknown as Record<string, unknown>).__mockRigs = [
      { app_id: "WSJT-X-A", config_name: "Home" },
      { app_id: "WSJT-X-B", config_name: "Portable" },
    ];
  });
  await expect(filter.locator("option")).toHaveText([
    "Any rig", "WSJT-X-A (Home)", "WSJT-X-B (Portable)",
  ]);
  await filter.selectOption("WSJT-X-B");
  await expect(filter).toHaveValue("WSJT-X-B");
  await expect(page.getByText(/WSJT-X rig filter: WSJT-X-B/)).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("wsjtxRigNameFilter"))).toBe("WSJT-X-B");

  await page.reload();
  await expect(filter).toHaveValue("WSJT-X-B");
  await expect(filter.locator("option:checked")).toHaveText("WSJT-X-B (saved)");
  await filter.selectOption("");
  await expect(filter).toHaveValue("");
  await expect(page.getByText(/WSJT-X rig filter cleared/)).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("wsjtxRigNameFilter"))).toBe("");
});

test("preserves a legacy config-name filter until a rig is selected", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("wsjtxRigNameFilter", "Home");
  });
  await page.goto("/");
  const filter = page.getByRole("combobox", { name: "Rig", exact: true });
  await expect(filter).toHaveValue("Home");
  await expect(filter.locator("option:checked")).toHaveText("Home (saved)");
});

test("keeps the app visible when an older sidecar omits rig discovery", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    (window as unknown as Record<string, unknown>).__mockLegacySidecar = true;
  });
  await page.goto("/");
  await expect(page.getByText(/Rig discovery unavailable: rebuild/)).toBeVisible();
  await page.waitForTimeout(2200);
  await expect(page.getByRole("combobox", { name: "Rig", exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("rejects malformed rig lists without crashing", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.addInitScript(() => {
    (window as unknown as Record<string, unknown>).__mockRigs = [null];
  });
  await page.goto("/");
  await expect(page.getByText(/Invalid WSJT-X rig list/)).toBeVisible();
  await expect(page.getByRole("combobox", { name: "Rig", exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
