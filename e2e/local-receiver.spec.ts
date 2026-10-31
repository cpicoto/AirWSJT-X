import { test, expect } from "@playwright/test";

test("Windows DDE output requires explicit enable and is not saved", async ({ page }) => {
  await page.addInitScript(() => {
    Object.assign(window, { __mockDDEStatus: { supported: true, enabled: false,
      service: "SatPC32", topic: "SatPcDdeConv", item: "SatPcDdeItem",
      target: "** NO SATELLITE **", error: null } });
  });
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByText("SatPC32 DDE output (Windows / PST Rotator)", { exact: true }).click();
  await expect(page.getByLabel("SatPC32 DDE status")).toContainText("DDE output off");
  expect(await page.evaluate(() =>
    (window as unknown as { __mockDDEEnableRequest?: boolean }).__mockDDEEnableRequest)).toBeUndefined();
  await page.getByLabel("Rotator output", { exact: true }).selectOption("dde");
  await expect(page.getByLabel("SatPC32 DDE status")).toContainText("DDE server enabled");
  await expect(page.getByLabel("SatPC32 DDE status")).toContainText("SatPcDdeItem");
  await expect(page.getByLabel("SatPC32 DDE status")).toContainText("Reference: MY station (A)");
  await expect(page.getByLabel("SatPC32 DDE status")).toContainText("SN is the selected flight identifier/callsign");
  await expect(page.getByLabel("SatPC32 DDE status")).toContainText("those are not transmitted");
  await page.getByLabel("Rotator output", { exact: true }).selectOption("off");
  await expect(page.getByLabel("SatPC32 DDE status")).toContainText("DDE output off");
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByText("SatPC32 DDE output (Windows / PST Rotator)", { exact: true }).click();
  await expect(page.getByLabel("Rotator output", { exact: true })).toHaveValue("off");
});

test("exclusive output ignores legacy activation and never resends displayed coordinates", async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.rotatorControlEnabled", "true");
    localStorage.setItem("airwsjtx.rotatorOutputPreference", "dde");
    Object.assign(window, { __mockDDEStatus: { supported: true, enabled: false,
      service: "SatPC32", topic: "SatPcDdeConv", item: "SatPcDdeItem",
      target: "** NO SATELLITE **", error: null } });
  });
  await page.reload({ waitUntil: "domcontentloaded" });
  const selector = page.getByLabel("Rotator output", { exact: true });
  await expect(selector).toHaveValue("off");
  await expect(page.getByRole("button", { name: "Enable DDE output", exact: true })).toHaveCount(0);
  await selector.selectOption("hamlib");
  await expect(page.getByLabel("Rotator output status")).toContainText("Hamlib selected");
  await selector.selectOption("dde");
  await expect(page.getByLabel("Rotator output status")).toContainText("Hamlib coordinate output disabled");
  await expect(page.getByRole("button", { name: "Connect", exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: /Park$/ })).toBeDisabled();
  expect(await page.evaluate(() => localStorage.getItem("airwsjtx.rotatorOutputPreference"))).toBe("dde");
  expect(await page.evaluate(() => localStorage.getItem("airwsjtx.rotatorControlEnabled"))).toBeNull();
  expect(await page.evaluate(() => (window as unknown as { __mockRotatorTarget?: unknown }).__mockRotatorTarget)).toBeUndefined();
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(selector).toHaveValue("off");
});

test("failed output switch shows error and keeps backend mode", async ({ page }) => {
  await page.evaluate(() => Object.assign(window, { __mockOutputError: "DDE service is already running" }));
  await page.getByLabel("Rotator output", { exact: true }).selectOption("hamlib");
  await expect(page.getByRole("alert")).toContainText("DDE service is already running");
  await expect(page.getByLabel("Rotator output", { exact: true })).toHaveValue("off");
});

test("DDE hands use published target without pretending to measure PST feedback", async ({ page }) => {
  await page.evaluate(() => Object.assign(window, {
    __mockOutputMode: "dde",
    __mockDDEStatus: { supported: true, enabled: true, service: "SatPC32",
      topic: "SatPcDdeConv", item: "SatPcDdeItem",
      target: "SNN4368V AZ294.9 EL1.7 UP0 UMUSB DN0 DMUSB MA0.0", error: null },
  }));
  const dials = page.getByTestId("rotator-dials");
  await expect(dials).toHaveAttribute("data-current-az", "294.9");
  await expect(dials).toHaveAttribute("data-target-az", "294.9");
  await expect(dials).toHaveAttribute("data-current-el", "1.7");
  await expect(dials).toHaveAttribute("data-target-el", "1.7");
  await expect(page.getByLabel("DDE dial reference")).toContainText("no measured PST position");
  await expect(page.getByText(/ΔAZ/)).toHaveCount(0);
  await expect(page.getByText(/ΔEL/)).toHaveCount(0);
  await page.evaluate(() => {
    const mock = window as unknown as { __mockDDEStatus: { target: string } };
    mock.__mockDDEStatus.target = "** NO SATELLITE **";
  });
  await expect(dials).toHaveAttribute("data-target-az", "");
  await expect(dials).toHaveAttribute("data-target-el", "");
  await expect(page.getByLabel("DDE dial reference")).toContainText("No valid published DDE target");
  await page.getByLabel("Rotator output", { exact: true }).selectOption("hamlib");
  await page.evaluate(() => {
    const emit = (window as unknown as { __tauriMockEmit: (event: string, payload: string) => void }).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({ event: "rotator_position", data: { azimuth: 280, elevation: 3 } }));
    emit("sidecar-message", JSON.stringify({ event: "plane_locked", data: {
      icao24: "abc123", callsign: "TEST", altitude_ft: 10000, az_a: 294.9, el_a: 1.7,
      az_b: 0, el_b: 0, scatter_score: 0.8, off_path_deg: 0, state: "tracking",
    } }));
  });
  await expect(dials).toHaveAttribute("data-current-az", "280");
  await expect(dials).toHaveAttribute("data-target-az", "294.9");
  await expect(page.getByText(/ΔAZ -14.9/)).toBeVisible();
  await expect(page.getByText(/ΔEL \+1.3/)).toBeVisible();
});

test("local locked position becomes stale at 120s using position provenance", async ({ page }) => {
  await page.clock.install({ time: new Date("2026-01-01T00:00:00Z") });
  await page.reload({ waitUntil: "domcontentloaded" });
  await expect(page.getByLabel("Rotator output", { exact: true })).toBeEnabled();
  await page.clock.pauseAt(new Date("2026-01-01T00:01:00Z"));
  await page.evaluate(() => {
    const emit = (window as unknown as { __tauriMockEmit: (event: string, payload: string) => void }).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({ event: "plane_locked", data: {
      icao24: "abc123", callsign: "N790TB", altitude_ft: 30000,
      az_a: 20, el_a: 11, az_b: 40, el_b: 5,
      scatter_score: 0.8, off_path_deg: 5, state: "tracking",
      position_updated_epoch_s: Date.now() / 1000,
      position_source: "rtlsdr", position_max_age_s: 120,
    } }));
  });
  const age = page.locator(".locked-update-age");
  await page.clock.runFor(119000);
  await expect(age).toContainText("119s ago");
  await expect(age).not.toContainText("stale");
  await page.clock.runFor(1000);
  await expect(age).toContainText("120s ago — stale");
  await expect(page.locator(".locked-plane")).toContainText("N790TB");
  await page.evaluate(() => {
    const emit = (window as unknown as { __tauriMockEmit: (event: string, payload: string) => void }).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({ event: "plane_tracking", data: {
      icao24: "abc123", az_a: 21, el_a: 10, scatter_score: 0.8,
      state: "tracking", position_updated_epoch_s: Date.now() / 1000,
      position_source: "rtlsdr", position_max_age_s: 120,
    } }));
  });
  await expect(age).toContainText("0s ago");
  await expect(age).not.toContainText("stale");
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => { Object.assign(window, { __mockReceiverSupported: true }); });
  await page.goto("/", { waitUntil: "domcontentloaded" });
  await page.getByText("Local RTL-SDR receiver (USB, Windows x64)", { exact: true }).click();
});

test("local receiver preview exposes all best-path ranking methods without starting capture", async ({ page }) => {
  const selector = page.getByRole("button", { name: "Best-path ranking", exact: true });
  await expect(selector).toBeVisible();
  await expect(selector).toHaveText("Legacy geometry (default)");
  await selector.click();
  await expect(page.getByRole("option", { name: "Range product", exact: true })).toBeVisible();
  await page.getByRole("option", { name: "Window-weighted range product", exact: true }).click();
  await expect(selector).toHaveText("Window-weighted range product");
  await expect.poll(() => page.evaluate(() => {
    const requests = (window as unknown as {
      __mockRpcRequests: { method: string; params: { filters?: { ranking_method?: string } } }[];
    }).__mockRpcRequests;
    return requests.filter(r => r.method === "airscout_apply_filters").at(-1)?.params.filters?.ranking_method;
  })).toBe("window-product");
  expect(await page.evaluate(() => Reflect.get(window, "__mockReceiverActive"))).not.toBe(true);
});

test("startup requests USB discovery once without enabling DDE", async ({ page }) => {
  await expect.poll(() => page.evaluate(() => {
    const requests = Reflect.get(window, "__mockRpcRequests") as { method: string }[];
    return requests.filter(r => r.method === "rtlsdr_auto_start").length;
  })).toBe(1);
  await page.waitForTimeout(2500);
  const requests = await page.evaluate(() => Reflect.get(window, "__mockRpcRequests") as { method: string }[]);
  expect(requests.filter(r => r.method === "rtlsdr_auto_start")).toHaveLength(1);
  expect(requests.filter(r => r.method === "satpc32_dde_set_enabled")).toHaveLength(0);
});

test("missing receiver is explicit and never starts a fake feed", async ({ page }) => {
  await expect(page.getByText("Gain changes restart only this receiver", { exact: false })).toHaveCount(0);
  await expect(page.getByLabel("Tuner gain", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Tuner gain", { exact: true })).toHaveValue("auto");
  await page.getByRole("button", { name: "Discover USB receivers", exact: true }).click();
  await expect(page.getByText("No accessible RTL-SDR receiver found.", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Start local ADS-B", exact: true })).toBeDisabled();
  expect(await page.evaluate(() =>
    (window as unknown as { __mockReceiverActive?: boolean }).__mockReceiverActive)).toBeUndefined();
});

test("discover selection start counters stop and network return", async ({ page }) => {
  await page.evaluate(() => {
    Object.assign(window, { __mockReceiverDevices: { devices: [
      { index: 0, name: "RTL2838", product: "NESDR", serial: "one", available: true, message: null,
        supported_gains_db: [0, 20.7, 32.8, 49.6] },
      { index: 1, name: "RTL2838", product: "NESDR", serial: "two", available: true, message: null,
        supported_gains_db: [0, 20.7, 32.8, 49.6] },
    ] } });
  });
  await page.getByRole("button", { name: "Discover USB receivers", exact: true }).click();
  await page.getByLabel("USB receiver", { exact: true }).selectOption("1");
  await expect(page.getByLabel("Tuner gain", { exact: true }).getByRole("option", { name: "32.8 dB (manual)" })).toHaveCount(1);
  await page.getByRole("button", { name: "Start local ADS-B", exact: true }).click();
  await expect(page.getByLabel("Local receiver status")).toContainText("18 CRC-valid ADS-B");
  await expect(page.getByLabel("IQ receive measurements")).toBeHidden();
  await page.getByText("Detailed IQ measurements", { exact: true }).click();
  await expect(page.getByLabel("IQ receive measurements")).toBeVisible();
  await expect(page.getByLabel("IQ receive measurements")).toContainText("20 preamble candidates");
  await expect(page.getByLabel("IQ receive measurements")).toContainText("2 DF17 CRC-rejected phase trials");
  await expect(page.getByLabel("IQ receive measurements")).toContainText("3 accepted other Mode S messages");
  await expect(page.getByLabel("Local receiver status")).toContainText("1 positioned");
  await expect(page.getByLabel("IQ receive measurements")).toContainText("2.4 MS/s");
  await expect(page.getByLabel("IQ receive measurements")).toContainText("0.010% ADC components clipped");
  await expect(page.getByLabel("IQ receive measurements")).toContainText("2 application queue buffers dropped");
  await expect(page.getByLabel("IQ receive measurements")).toContainText("not measurable");
  await page.getByText("Last validated DF17 payloads", { exact: false }).click();
  await expect(page.getByText("8d40621d58c382d690c8ac2863a7", { exact: false })).toBeVisible();
  await expect(page.getByLabel("USB receiver", { exact: true })).toBeDisabled();
  await expect(page.getByText("LOCAL ADS-B", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Tuner gain", { exact: true })).toHaveValue("auto");
  await page.getByLabel("Tuner gain", { exact: true }).selectOption("32.8");
  await page.getByRole("button", { name: "Apply gain / restart", exact: true }).click();
  await expect(page.getByLabel("Local receiver status")).toContainText("0 CRC-valid ADS-B");
  await expect(page.getByLabel("Tuner gain", { exact: true })).toHaveValue("32.8");
  await page.getByLabel("Tuner gain", { exact: true }).selectOption("auto");
  await page.getByRole("button", { name: "Apply gain / restart", exact: true }).click();
  await expect(page.getByLabel("Tuner gain", { exact: true })).toHaveValue("auto");
  const requests = await page.evaluate(() =>
    (window as unknown as { __mockRpcRequests: { method: string; params: Record<string, unknown> }[] }).__mockRpcRequests);
  expect(requests.find(request => request.method === "rtlsdr_start")?.params.device_index).toBe(1);
  await page.getByRole("button", { name: "Stop local receiver", exact: true }).click();
  await expect(page.getByLabel("Local receiver status")).toContainText("USB device released");
  await expect(page.getByLabel("USB receiver", { exact: true })).toBeEnabled();
  expect(await page.evaluate(() =>
    (window as unknown as { __mockReceiverActive: boolean }).__mockReceiverActive)).toBe(false);
});

test("manual gain can be selected before receiver start", async ({ page }) => {
  await page.evaluate(() => Object.assign(window, { __mockReceiverDevices: { devices: [
    { index: 0, name: "RTL2838", product: "NESDR", serial: "one", available: true, message: null,
      supported_gains_db: [0, 20.7, 32.8, 49.6] },
  ] } }));
  await page.getByRole("button", { name: "Discover USB receivers", exact: true }).click();
  await page.getByLabel("Tuner gain", { exact: true }).selectOption("32.8");
  await page.getByRole("button", { name: "Start local ADS-B", exact: true }).click();
  await expect(page.getByLabel("Tuner gain", { exact: true })).toHaveValue("32.8");
  const gain = await page.evaluate(() => (window as unknown as {
    __mockRpcRequests: { method: string; params: { gain_db: number } }[];
  }).__mockRpcRequests.find(request => request.method === "rtlsdr_start")?.params.gain_db);
  expect(gain).toBe(32.8);
});

test("lost backend reply times out visibly and status polling recovers", async ({ page }) => {
  await page.evaluate(() => Object.assign(window, { __mockRpcNoReply: ["rtlsdr_discover", "rtlsdr_status"] }));
  await page.clock.install();
  await page.getByRole("button", { name: "Discover USB receivers", exact: true }).click();
  await page.clock.runFor(11000);
  await expect(page.getByRole("alert")).toContainText("Backend did not reply");
  await expect(page.getByRole("button", { name: "Discover USB receivers", exact: true })).toBeEnabled();
  await page.evaluate(() => Object.assign(window, {
    __mockRpcNoReply: [],
    __mockReceiverStatus: { state: "stopped", message: "Backend recovered" },
  }));
  await page.clock.runFor(14000);
  await expect(page.getByLabel("Local receiver status")).toContainText("Backend recovered");
});

test("native push updates refresh measurements without waiting for status polling", async ({ page }) => {
  await page.evaluate(() => {
    Object.assign(window, { __mockRpcNoReply: ["rtlsdr_status"] });
    (window as unknown as {
      __tauriMockEmit: (event: string, payload: string) => void;
    }).__tauriMockEmit("sidecar-message", JSON.stringify({
      event: "rtlsdr_update",
      data: {
        state: "connected", message: "Native receive measurements",
        frames: 25, valid_messages: 2, crc_errors: 3, unsupported_frames: 20,
        iq: {
          samples: 4800000, sample_rate: 2400000, elapsed_s: 2,
          clipped_fraction: 0, mean_power_dbfs: -12, signal_power_dbfs: null,
          candidate_quiet_magnitude: 0.1, dropped_buffers: 0, discontinuities: 0,
          usb_overruns: null,
        },
      },
    }));
  });
  await expect(page.getByLabel("Local receiver status")).toContainText("2 CRC-valid ADS-B");
  await expect(page.getByLabel("IQ receive measurements")).toContainText("4,800,000");
});

test("busy driver errors are visible", async ({ page }) => {
  await page.evaluate(() => Object.assign(window, {
    __mockRpcErrors: { rtlsdr_discover: "USB device busy. Close the other SDR app." },
  }));
  await page.getByRole("button", { name: "Discover USB receivers", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("USB device busy");
});
