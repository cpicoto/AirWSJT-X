import { test, expect } from "@playwright/test";

async function emitDecode(page: import("@playwright/test").Page, data: Record<string, unknown>) {
  await page.evaluate(data => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "wsjtx_decode",
      data: { message: "CQ K7MDL CN87", snr: -10, delta_freq_hz: 1000, mode: "FT8", ...data },
    }));
  }, data);
}

for (const timezoneId of ["Europe/Lisbon", "Pacific/Honolulu"]) {
  test.describe(`decode timestamps in ${timezoneId}`, () => {
    test.use({ timezoneId });

    test("uses the WSJT-X UTC time instead of browser-local arrival time", async ({ page }) => {
      await page.goto("/");
      await emitDecode(page, { time_ms: 1155000 });
      const row = page.locator(".decode-row").first();
      await expect(page.getByRole("heading", { name: /WSJT-X Decodes \(UTC\)/ })).toBeVisible();
      await expect(row.locator(".decode-time")).toHaveText("001915");
    });
  });
}

test("keeps delayed and replayed decode source times in arrival order", async ({ page }) => {
  await page.goto("/");
  await emitDecode(page, { message: "END OF DAY", time_ms: 86399999 });
  await emitDecode(page, { message: "REPLAYED MIDNIGHT", time_ms: 0 });
  const rows = page.locator(".decode-row");
  await expect(rows.nth(0)).toContainText("000000");
  await expect(rows.nth(0)).toContainText("REPLAYED MIDNIGHT");
  await expect(rows.nth(1)).toContainText("235959");
  await expect(rows.nth(1)).toContainText("END OF DAY");
});

test("rejects missing and invalid times without truncating valid six-digit times", async ({ page }) => {
  await page.goto("/");
  for (const time_ms of [undefined, "1155000", 1155000.5, -1, 86400000, 0xFFFFFFFF]) {
    await emitDecode(page, { message: `INVALID ${String(time_ms)}`, time_ms });
  }
  await emitDecode(page, { message: "VALID", time_ms: 86399999 });

  const validTime = page.locator(".decode-row").first().locator(".decode-time");
  await expect(validTime).toHaveText("235959");
  expect(await validTime.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  await expect(page.locator(".decode-time").filter({ hasText: "—" })).toHaveCount(6);
});

test("shows UDP bind errors outside the log and clears them after reception recovers", async ({ page }) => {
  await page.setViewportSize({ width: 1000, height: 720 });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Rig", exact: true })).toBeVisible();
  const message = "Cannot listen for WSJT-X UDP on port 2237: port in use. Configure Log4OM UDP forwarding.";
  await page.evaluate(message => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({ event: "wsjtx_error", data: { message } }));
  }, message);
  const alert = page.getByRole("alert", { name: "WSJT-X UDP error" });
  await expect(alert).toHaveText(message);
  await expect(page.locator(".topbar")).toContainText("WSJT-X not connected");
  expect(await alert.evaluate(element => element.parentElement?.classList.contains("display-toolbar"))).toBe(true);
  const contentBox = await page.locator(".content").boundingBox();
  const logBox = await page.locator(".log-list").boundingBox();
  expect(contentBox?.height).toBeGreaterThan(450);
  expect(logBox?.height).toBeGreaterThan(30);
  await expect(page.locator(".log-list")).toContainText("WSJT-X error");
  await page.evaluate(() => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "wsjtx_status", data: { de_call: "K7MDL", mode: "FT8", freq_mhz: 18.1 },
    }));
  });
  await expect(alert).toHaveCount(0);
  await expect(page.locator(".topbar")).toContainText("K7MDL");
});

test("restores a UDP error from status when the startup event was missed", async ({ page }) => {
  const message = "Cannot listen for WSJT-X UDP on port 2237: port in use. Retrying automatically.";
  await page.addInitScript(message => {
    (window as unknown as Record<string, unknown>).__mockWsjtxError = message;
  }, message);
  await page.goto("/");
  const alert = page.getByRole("alert", { name: "WSJT-X UDP error" });
  await expect(alert).toHaveText(message);
  await page.reload();
  await expect(alert).toHaveText(message);
  await page.evaluate(() => {
    (window as unknown as Record<string, unknown>).__mockWsjtxError = null;
  });
  await expect(alert).toHaveCount(0);
  await expect(page.locator(".topbar")).toContainText("WSJT-X not connected");
});

test("shows remaining quota and server retry time without inventing ADS-B Exchange quota", async ({ page }) => {
  await page.addInitScript(() => {
    Object.assign(window, { __mockQuotas: {
      opensky: { remaining: 4000, checked_epoch_s: 1000, retry_epoch_s: 2000, check_interval_s: 1800 },
    } });
  });
  await page.goto("/");
  await page.getByText("Aircraft accounts", { exact: true }).click();
  await expect(page.getByText(/Remaining credits: 4000/)).toBeVisible();
  await expect(page.getByText(/refresh 30 min/)).toBeVisible();
  await expect(page.getByText(/retry no earlier than/)).toBeVisible();
  await expect(page.getByText("Remaining quota: not exposed by the documented API", { exact: true })).toBeVisible();
});

test("poll rates can be saved independently of secrets and enforce provider minimums", async ({ page }) => {
  await page.setViewportSize({ width: 900, height: 600 });
  await page.goto("/");
  await page.getByText("Aircraft accounts", { exact: true }).click();
  const openSky = page.getByLabel("OpenSky poll interval (seconds)", { exact: true });
  const adsb = page.getByLabel("ADS-B Exchange poll interval (seconds)", { exact: true });
  await expect(openSky).toHaveValue("30");
  await expect(adsb).toHaveValue("30");
  await openSky.fill("9");
  await page.getByRole("button", { name: "Set OpenSky poll rate", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("at least 10");
  await openSky.fill("10");
  await page.getByRole("button", { name: "Set OpenSky poll rate", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("10 seconds");
  await adsb.fill("4");
  await page.getByRole("button", { name: "Set ADS-B Exchange poll rate", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("at least 5");
  await adsb.fill("5");
  await page.getByRole("button", { name: "Set ADS-B Exchange poll rate", exact: true }).click();
  await expect(page.getByRole("status")).toContainText("5 seconds");
  await expect(page.getByLabel("OpenSky Client Secret", { exact: true })).toHaveValue("");
  await page.getByText("Aircraft accounts", { exact: true }).click();
  await page.getByText("Aircraft accounts", { exact: true }).click();
  await expect(openSky).toHaveValue("10");
  await expect(adsb).toHaveValue("5");
});

test("account fields save without browser persistence and clear masked secrets", async ({ page }) => {
  await page.goto("/");
  await page.getByText("Aircraft accounts", { exact: true }).click();
  await page.getByLabel("OpenSky Client ID", { exact: true }).fill("test-client");
  await page.getByLabel("OpenSky Client Secret", { exact: true }).fill("fake-test-secret");
  await expect(page.getByLabel("OpenSky Client Secret", { exact: true })).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Save OpenSky", exact: true }).click();
  await expect(page.getByText("OpenSky (account saved)", { exact: true })).toBeVisible();
  await expect(page.getByLabel("OpenSky Client Secret", { exact: true })).toHaveValue("");
  await page.getByLabel("ADS-B Exchange API key", { exact: true }).fill("fake-test-key");
  await expect(page.getByLabel("ADS-B Exchange API key", { exact: true })).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "Save ADS-B Exchange", exact: true }).click();
  await expect(page.getByLabel("ADS-B Exchange API key", { exact: true })).toHaveValue("");
  const settings = await page.evaluate(() => JSON.stringify(localStorage));
  expect(settings).not.toContain("fake-test-secret");
  expect(settings).not.toContain("fake-test-key");
  expect(await page.evaluate(() => JSON.stringify(sessionStorage))).not.toContain("fake-test");
  await page.getByRole("button", { name: "Clear OpenSky", exact: true }).click();
  await expect(page.getByText("OpenSky (anonymous)", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Clear ADS-B Exchange", exact: true }).click();
  await expect(page.getByText("ADS-B Exchange (API key saved)", { exact: true })).not.toBeVisible();
});

test("restores saved account status without populating secrets", async ({ page }) => {
  await page.addInitScript(() => {
    Object.assign(window, { __mockCredentialStatus: {
      opensky_client_id: "saved-client", opensky_configured: true, adsbexchange_configured: true,
      opensky_poll_interval_s: 30, adsbexchange_poll_interval_s: 30,
    } });
  });
  await page.goto("/");
  await page.getByText("Aircraft accounts", { exact: true }).click();
  await expect(page.getByLabel("OpenSky Client ID", { exact: true })).toHaveValue("saved-client");
  await expect(page.getByLabel("OpenSky Client Secret", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("ADS-B Exchange API key", { exact: true })).toHaveValue("");
});

test("reports account save errors without claiming success", async ({ page }) => {
  await page.addInitScript(() => {
    Object.assign(window, { __mockCredentialSaveError: "Aircraft backend is not ready" });
  });
  await page.goto("/");
  await page.getByText("Aircraft accounts", { exact: true }).click();
  await page.getByLabel("OpenSky Client ID", { exact: true }).fill("test-client");
  await page.getByLabel("OpenSky Client Secret", { exact: true }).fill("fake-test-secret");
  await page.getByRole("button", { name: "Save OpenSky", exact: true }).click();
  await expect(page.getByRole("status")).toHaveText("Aircraft backend is not ready");
  await expect(page.getByText("OpenSky (account saved)", { exact: true })).not.toBeVisible();
});

test("ADS-B Exchange can be enabled and selected as the online source", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "+ ADS-B Exchange", exact: true }).click();
  const source = page.getByRole("button", { name: "✓ ADS-B Exchange", exact: true });
  await expect(source).toBeVisible();
  await source.locator("..").getByRole("button", { name: "↑", exact: true }).click();
  await expect(page.locator(".topbar")).toContainText("adsbexchange");
  await expect(page.locator(".topbar")).toContainText("NET ONLINE");
});

test("manual MY station works without WSJT-X and survives incoming status", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("textbox", { name: "MY callsign", exact: true }).fill("K7MDL");
  await page.getByRole("textbox", { name: "MY grid", exact: true }).fill("CN87VP");
  await page.getByRole("textbox", { name: "MY grid", exact: true }).press("Tab");
  await expect(page.getByRole("textbox", { name: "MY grid", exact: true })).toHaveValue("CN87VP");
  await page.getByRole("button", { name: "Apply MY station" }).click();
  await expect(page.locator(".station-pill").first()).toContainText("K7MDL");
  await expect(page.locator(".station-pill").first()).toContainText("CN87VP");
  await page.evaluate(() => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "wsjtx_status", data: { de_call: "W1AW", de_grid: "FN31PR" },
    }));
  });
  await expect(page.locator(".station-pill").first()).toContainText("K7MDL");
  await expect(page.locator(".station-pill").first()).toContainText("CN87VP");
  await page.reload();
  await expect(page.locator(".station-pill").first()).toContainText("K7MDL");
  await expect(page.getByRole("textbox", { name: "MY grid", exact: true })).toHaveValue("CN87VP");
  await page.getByRole("button", { name: "MY station source", exact: true }).click();
  await expect(page.getByText(/MY station reset to WSJT-X/)).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("airwsjtx.myCallOverride"))).toBe("");
});

test("discovers rigs, selects and persists a filter, and clears it", async ({ page }) => {
  await page.goto("/");
  const filter = page.getByRole("button", { name: "Rig", exact: true });
  await expect(filter).toHaveText("Any rig");
  await page.evaluate(() => {
    (window as unknown as Record<string, unknown>).__mockRigs = [
      { app_id: "WSJT-X-A", config_name: "Home" },
      { app_id: "WSJT-X-B", config_name: "Portable" },
    ];
  });
  await expect(async () => {
    await filter.click();
    await expect(page.getByRole("listbox").getByRole("option")).toHaveText([
      "Any rig", "WSJT-X-A (Home)", "WSJT-X-B (Portable)",
    ], { timeout: 500 });
  }).toPass();
  await page.getByRole("option", { name: "WSJT-X-B (Portable)" }).click();
  await expect(filter).toHaveText("WSJT-X-B (Portable)");
  await expect(page.getByText(/WSJT-X rig filter: WSJT-X-B/)).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("wsjtxRigNameFilter"))).toBe("WSJT-X-B");

  await page.reload();
  await expect(filter).toHaveText("WSJT-X-B (saved)");
  await filter.click();
  await page.getByRole("option", { name: "Any rig" }).click();
  await expect(filter).toHaveText("Any rig");
  await expect(page.getByText(/WSJT-X rig filter cleared/)).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem("wsjtxRigNameFilter"))).toBe("");
});

test("preserves a legacy config-name filter until a rig is selected", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("wsjtxRigNameFilter", "Home");
  });
  await page.goto("/");
  await expect(page.getByRole("button", { name: "Rig", exact: true })).toHaveText("Home (saved)");
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
  await expect(page.getByRole("button", { name: "Rig", exact: true })).toBeVisible();
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
  await expect(page.getByRole("button", { name: "Rig", exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});
