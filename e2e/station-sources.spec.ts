import { test, expect, type Page } from "@playwright/test";

async function wsjtx(page: Page, data: Record<string, unknown>) {
  await page.evaluate(data => {
    const emit = Reflect.get(window, "__tauriMockEmit") as (event: string, payload: string) => void;
    emit("sidecar-message", JSON.stringify({
      event: "wsjtx_status",
      data: { de_call: "W1AW", de_grid: "CN87", dx_call: "K7DX", dx_grid: "CN89", ...data },
    }));
  }, data);
}

async function lastRpc(page: Page, method: string) {
  return page.evaluate(method => {
    const requests = Reflect.get(window, "__mockRpcRequests") as {
      method: string; params: Record<string, unknown>;
    }[];
    return requests?.filter(r => r.method === method).at(-1)?.params;
  }, method);
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    Object.assign(window, {
      __mockRemoteDb: [
        { name: "W1AW@home", grid6: "FN31PR" },
        { name: "K7DX@home", grid6: "FN32AA" },
      ],
    });
  });
});

async function ready(page: Page) {
  await page.goto("/");
  await expect(page.getByText(/Remote station DB loaded/).first()).toBeVisible();
  await expect.poll(() => lastRpc(page, "get_status")).toBeDefined();
}

test("MY follows valid WSJT-X grids ahead of callbook, while explicit local overrides survive", async ({ page }) => {
  await ready(page);
  await wsjtx(page, {});
  await expect(page.getByRole("button", { name: "MY station source" })).toHaveText("WSJT-X");
  await expect(page.getByRole("textbox", { name: "MY grid", exact: true })).toHaveValue("CN87");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_a: "CN87", locator_b: "CN89" });

  await wsjtx(page, { de_grid: "CN88" });
  await expect(page.getByRole("textbox", { name: "MY grid", exact: true })).toHaveValue("CN88");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_a: "CN88", locator_b: "CN89" });
  await page.getByRole("textbox", { name: "MY grid", exact: true }).fill("CN86VP");
  await page.getByRole("button", { name: "Apply MY station" }).click();
  await expect(page.getByRole("button", { name: "MY station source" })).toHaveText("Local");
  await wsjtx(page, { de_grid: "CN85" });
  await expect(page.locator(".station-pill").first()).toContainText("CN86VP");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_a: "CN86VP" });
  await page.getByRole("button", { name: "MY station source" }).click();
  await expect(page.getByRole("textbox", { name: "MY grid", exact: true })).toHaveValue("CN85");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_a: "CN85" });
});

test("a favorite's grid and altitude beat stale callbook and portable grids without debounce writeback", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.favoriteDx", JSON.stringify([{ call: "K7DX", grid: "CN90AA", alt: "345" }]));
    localStorage.setItem("airwsjtx.portableGridOverrides", JSON.stringify({ K7DX: "CN85" }));
  });
  await ready(page);
  await wsjtx(page, {});
  await page.getByRole("combobox", { name: "DX favorites" }).selectOption("0");
  await expect(page.getByRole("button", { name: "DX station source" })).toHaveText("Local");
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("CN90AA");
  await expect(page.getByRole("textbox", { name: "DX altitude (m)", exact: true })).toHaveValue("345");
  await expect.poll(() => lastRpc(page, "set_dx_altitude")).toEqual({ altitude_m: 345 });
  await page.waitForTimeout(750);
  await expect(page.locator(".station-pill").last()).toContainText("CN90AA");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({
    locator_a: "CN87", locator_b: "CN90AA", dx_local: true,
  });
  await expect.poll(() => lastRpc(page, "set_station_calls")).toEqual({ call_a: "W1AW", call_b: "K7DX" });

  await wsjtx(page, { de_grid: "CN88", dx_call: "W7NEW", dx_grid: "CN84" });
  await expect(page.getByRole("textbox", { name: "DX callsign", exact: true })).toHaveValue("K7DX");
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("CN90AA");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_a: "CN88", locator_b: "CN90AA", dx_local: true });

  await page.getByRole("textbox", { name: "DX grid", exact: true }).fill("CN91BB");
  await page.getByRole("textbox", { name: "DX grid", exact: true }).press("Tab");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: "CN91BB", dx_local: true });
  await expect(page.locator(".station-pill").last()).toContainText("CN91BB");
});

test("same-call WSJT-X grid removal falls back to callbook and later valid updates replace it", async ({ page }) => {
  await ready(page);
  await wsjtx(page, {});
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("CN89");
  await wsjtx(page, { dx_grid: null });
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("FN32AA");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: "FN32AA", dx_local: false });
  await wsjtx(page, { dx_grid: "CN90" });
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("CN90");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: "CN90", dx_local: false });
  await wsjtx(page, { dx_grid: "INVALID" });
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("FN32AA");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: "FN32AA" });
});

test("source toggling with unchanged call/grid still synchronizes the backend source", async ({ page }) => {
  await ready(page);
  await wsjtx(page, {});
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("CN89");
  await page.getByRole("button", { name: "DX station source" }).click();
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: "CN89", dx_local: true });
  await page.getByRole("button", { name: "DX station source" }).click();
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: "CN89", dx_local: false });
  await wsjtx(page, { dx_grid: "CN90" });
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: "CN90", dx_local: false });
});

test("missing WSJT-X and callbook grid clears stale backend DX geometry", async ({ page }) => {
  await ready(page);
  await wsjtx(page, { dx_call: "W7NEW" });
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("CN89");
  await wsjtx(page, { dx_call: "W7NEW", dx_grid: null });
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: null, dx_local: false });
});

test("DX altitude restore and favorite/user actions log sidecar failures", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.dxAltitudeM", "123");
    Object.assign(window, { __mockRpcErrors: { set_dx_altitude: "Altitude rejected" } });
  });
  await ready(page);
  await expect(page.getByText(/DX altitude restore error:.*Altitude rejected/)).toBeVisible();
  await page.getByRole("combobox", { name: "DX favorites" }).selectOption("0");
  await expect(page.getByText(/DX altitude error:.*Altitude rejected/).first()).toBeVisible();
  await page.getByRole("textbox", { name: "DX altitude (m)", exact: true }).fill("250");
  await page.getByRole("button", { name: "Store DX station" }).click();
  await expect.poll(() => lastRpc(page, "set_dx_altitude")).toEqual({ altitude_m: 250 });
  await expect(page.getByText(/DX altitude error:.*Altitude rejected/)).toHaveCount(2);
});

test("favorites stay FIFO capped at 20 and deduplicate only exact station matches", async ({ page }) => {
  await page.addInitScript(() => {
    if (!localStorage.getItem("airwsjtx.favoriteDx")) {
      localStorage.setItem("airwsjtx.favoriteDx", JSON.stringify(Array.from({ length: 20 }, (_, i) => ({
        call: `K7T${String(i).padStart(2, "0")}`, grid: "CN89", alt: "0",
      }))));
    }
  });
  await ready(page);
  await wsjtx(page, {});
  const favorites = page.getByRole("combobox", { name: "DX favorites" });
  const saved = () => page.evaluate(() => JSON.parse(localStorage.getItem("airwsjtx.favoriteDx")!) as {
    call: string; grid: string; alt: string;
  }[]);
  await favorites.selectOption("19");
  expect((await saved())[0].call).toBe("K7T00");
  await page.getByRole("button", { name: "Store DX station" }).click();
  await expect.poll(async () => (await saved())[0].call).toBe("K7T19");
  expect(await saved()).toHaveLength(20);
  expect((await saved()).filter(f => f.call === "K7T19")).toHaveLength(1);

  await page.getByRole("textbox", { name: "DX altitude (m)", exact: true }).fill("250");
  await page.getByRole("button", { name: "Store DX station" }).click();
  await expect.poll(async () => (await saved())[0].alt).toBe("250");
  expect(await saved()).toHaveLength(20);
  expect((await saved()).filter(f => f.call === "K7T19")).toHaveLength(2);
  expect((await saved()).some(f => f.call === "K7T18")).toBe(false);
  await page.reload();
  await expect(favorites.locator("option")).toHaveCount(21);
  await expect(favorites.locator("option").nth(1)).toContainText("250 m");
});
