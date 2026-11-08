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

test("last used local DX station and altitude are restored on startup", async ({ page }) => {
  await ready(page);
  await page.getByRole("button", { name: "DX favorites", exact: true }).click();
  await page.getByRole("listbox").getByRole("option").first().click();
  const call = await page.getByRole("textbox", { name: "DX callsign", exact: true }).inputValue();
  const grid = (await page.getByRole("textbox", { name: "DX grid", exact: true }).inputValue()).toUpperCase();
  await page.getByRole("textbox", { name: "DX altitude (m)", exact: true }).fill("345");
  await page.getByRole("button", { name: "Store DX station" }).click();
  await page.reload();
  await expect(page.getByRole("textbox", { name: "DX callsign", exact: true })).toHaveValue(call);
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue(grid);
  await expect(page.getByRole("textbox", { name: "DX altitude (m)", exact: true })).toHaveValue("345");
  await expect(page.getByRole("button", { name: "DX station source" })).toHaveText("Local");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: grid, dx_local: true });
});

test("last WSJT-X DX is restored until fresh WSJT-X data arrives", async ({ page }) => {
  await ready(page);
  await wsjtx(page, { dx_call: "K7DX", dx_grid: "CN90AA" });
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("CN90AA");
  await expect.poll(() => page.evaluate(() => localStorage.getItem("airwsjtx.lastDx")))
    .toContain('"call":"K7DX"');
  await page.reload();
  await expect(page.getByRole("textbox", { name: "DX callsign", exact: true })).toHaveValue("K7DX");
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("CN90AA");
  await expect(page.getByRole("button", { name: "DX station source" })).toHaveText("WSJT-X");
  await wsjtx(page, { dx_call: "W7NEW", dx_grid: "CN84" });
  await expect(page.getByRole("textbox", { name: "DX callsign", exact: true })).toHaveValue("W7NEW");
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("CN84");
});

test("a favorite's grid and altitude beat stale callbook and portable grids without debounce writeback", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.favoriteDx", JSON.stringify([{ call: "K7DX", grid: "CN90AA", alt: "345" }]));
    localStorage.setItem("airwsjtx.portableGridOverrides", JSON.stringify({ K7DX: "CN85" }));
  });
  await ready(page);
  await wsjtx(page, {});
  await page.getByRole("button", { name: "DX favorites", exact: true }).click();
  await page.getByRole("listbox").getByRole("option").first().click();
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
  await page.getByRole("button", { name: "DX favorites", exact: true }).click();
  await page.getByRole("listbox").getByRole("option").first().click();
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
  const favorites = page.getByRole("button", { name: "DX favorites", exact: true });
  const saved = () => page.evaluate(() => JSON.parse(localStorage.getItem("airwsjtx.favoriteDx")!) as {
    call: string; grid: string; alt: string;
  }[]);
  await favorites.click();
  await page.getByRole("listbox").getByRole("option").nth(19).click();
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
  await favorites.click();
  // The saved list is still capped; offline built-ins remain selectable after it.
  await expect(page.getByRole("listbox").getByRole("option")).toHaveCount(22);
  await expect(page.getByRole("listbox").getByRole("option").first()).toContainText("250 m");
});

test("DB Update refreshes searchable DX choices, selects downloaded grids, and persists across reload", async ({ page }) => {
  await page.addInitScript(() => {
    Object.assign(window, {
      __mockCallsignDbDownload: {
        version: "test-db",
        entries: {
          K7MDL: { name: "Downloaded station", grid: "CN87XS", lat: 47.77, lon: -122.04, country: "USA" },
          KD7UO: { name: null, grid: "CN88QK", lat: null, lon: null, country: null },
          VE7NX: { name: null, grid: "CN89JI", lat: null, lon: null, country: null },
        },
      },
    });
  });
  await ready(page);
  await wsjtx(page, {});
  const picker = page.getByRole("button", { name: "DX favorites", exact: true });
  await picker.click();
  await expect(page.getByRole("listbox").getByRole("option")).toHaveCount(2);
  await picker.press("Escape");
  await page.getByRole("button", { name: "DB Update", exact: true }).click();
  await expect(page.getByText("Callsign DB downloaded (3 entries, test-db)", { exact: false })).toBeVisible();
  await picker.click();
  const options = page.getByRole("listbox").getByRole("option");
  await expect(options).toHaveCount(4);
  await expect(options.first()).toContainText("VE7NX");
  await expect(options.nth(1)).toContainText("W7PUA");
  await expect(options.nth(2)).toContainText("K7MDL");
  await picker.press("Escape");
  const search = page.getByRole("textbox", { name: "Search callsign database" });
  await search.fill("k7mdl");
  await picker.click();
  await expect(options).toHaveCount(1);
  await options.first().click();
  await expect(page.getByRole("textbox", { name: "DX callsign", exact: true })).toHaveValue("K7MDL");
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("CN87XS");
  await expect(page.getByRole("textbox", { name: "DX altitude (m)", exact: true })).toHaveValue("0");
  await expect(page.getByRole("button", { name: "DX station source" })).toHaveText("Local");
  await expect.poll(() => lastRpc(page, "airscout_set_stations"))
    .toMatchObject({ locator_a: "CN87", locator_b: "CN87XS", dx_local: true });
  await expect.poll(() => lastRpc(page, "set_station_calls")).toEqual({ call_a: "W1AW", call_b: "K7MDL" });
  await expect.poll(() => page.evaluate(() => localStorage.getItem("airwsjtx.lastDx")))
    .toContain('"grid":"CN87XS"');
  // Selecting a database station does not silently store it as a favorite.
  expect(await page.evaluate(() => localStorage.getItem("airwsjtx.favoriteDx"))).toBeNull();
  await page.reload();
  await expect(page.getByRole("textbox", { name: "DX callsign", exact: true })).toHaveValue("K7MDL");
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("CN87XS");
  await search.fill("KD7UO");
  await picker.click();
  await expect(options).toHaveCount(1);
  await options.first().click();
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: "CN88QK", dx_local: true });
});

test("downloaded choices honor user grid overrides and typing resolves the offline database", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("__mock.callsignDb", JSON.stringify({
      version: "test-db",
      entries: {
        K7MDL: { name: null, grid: "CN87XS", lat: null, lon: null, country: null },
        KD7UO: { name: null, grid: "CN88QK", lat: null, lon: null, country: null },
      },
    }));
    localStorage.setItem("airwsjtx.portableGridOverrides", JSON.stringify({ K7MDL: "CN90AA" }));
  });
  await ready(page);
  await wsjtx(page, {});
  await page.getByRole("button", { name: "DX favorites", exact: true }).click();
  await page.getByRole("option", { name: /K7MDL/ }).click();
  await expect.poll(() => lastRpc(page, "airscout_set_stations"))
    .toMatchObject({ locator_b: "CN90AA", dx_local: true });
  // A new call with no entered grid falls back to downloaded entries without a network lookup.
  await page.getByRole("textbox", { name: "DX grid", exact: true }).fill("");
  await page.getByRole("textbox", { name: "DX callsign", exact: true }).fill("KD7UO");
  await page.getByRole("textbox", { name: "DX callsign", exact: true }).press("Tab");
  await expect.poll(() => lastRpc(page, "airscout_set_stations"))
    .toMatchObject({ locator_b: "CN88QK", dx_local: true });
});

test("coordinate-only records select a grid through the existing DX geometry flow", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("__mock.callsignDb", JSON.stringify({
      version: "coordinates",
      entries: { K7MDL: { name: null, grid: null, lat: 47.77, lon: -122.04, country: null } },
    }));
  });
  await ready(page);
  await page.getByRole("button", { name: "DX favorites", exact: true }).click();
  await page.getByRole("option", { name: /K7MDL/ }).click();
  await expect(page.getByRole("textbox", { name: "DX grid", exact: true })).toHaveValue("CN87XS");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: "CN87XS", dx_local: true });
});

test("database choices are not capped by favorites and refreshing replaces stale records", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("__mock.callsignDb", JSON.stringify({
      version: "large",
      entries: Object.fromEntries(Array.from({ length: 30 }, (_, i) => [
        `K7T${String(i).padStart(2, "0")}`,
        { name: `Database ${i}`, grid: "CN88QK", lat: null, lon: null, country: null },
      ])),
    }));
    Object.assign(window, {
      __mockCallsignDbDownload: {
        version: "replacement",
        entries: { AD7NP: { name: null, grid: "CN87VP", lat: null, lon: null, country: null } },
      },
    });
  });
  await ready(page);
  const picker = page.getByRole("button", { name: "DX favorites", exact: true });
  await picker.click();
  await expect(page.getByRole("listbox").getByRole("option")).toHaveCount(32);
  await picker.press("End");
  await expect(page.getByRole("option", { name: /K7T29/ })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("textbox", { name: "DX callsign", exact: true })).toHaveValue("K7T29");
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: "CN88QK" });
  await page.getByRole("button", { name: "DB Update", exact: true }).click();
  await picker.click();
  await expect(page.getByRole("listbox").getByRole("option")).toHaveCount(3);
  await expect(page.getByRole("option", { name: /K7T29/ })).toHaveCount(0);
  await page.getByRole("option", { name: /AD7NP/ }).click();
  await expect.poll(() => lastRpc(page, "airscout_set_stations")).toMatchObject({ locator_b: "CN87VP" });
});

test("failed choice refresh reports the error and keeps existing choices usable", async ({ page }) => {
  await ready(page);
  await expect.poll(() => lastRpc(page, "callsign_db_entries")).toBeDefined();
  await page.evaluate(() => {
    Object.assign(window, {
      __mockRpcErrors: { callsign_db_entries: "Cannot list stations" },
      __mockCallsignDbDownload: { version: "test", entries: {} },
    });
  });
  await page.getByRole("button", { name: "DB Update", exact: true }).click();
  await expect(page.getByText(/choices could not be refreshed:.*Cannot list stations/)).toBeVisible();
  await page.getByRole("button", { name: "DX favorites", exact: true }).click();
  await expect(page.getByRole("listbox").getByRole("option")).toHaveCount(2);
  await page.getByRole("option", { name: /W7PUA/ }).click();
  await expect(page.getByRole("textbox", { name: "DX callsign", exact: true })).toHaveValue("W7PUA");
});
