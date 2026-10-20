import { test, expect } from "@playwright/test";

// Inject a mock speechSynthesis into the page before each test.
// The mock records all speak() calls and the cancel() / resume() history.
async function installSpeechMock(page: Parameters<typeof test>[1]["page"]) {
  await page.addInitScript(() => {
    const calls: string[] = [];
    let cancelled = 0;
    let resumed = 0;
    let paused = false;
    const mock = {
      get paused() { return paused; },
      set _paused(v: boolean) { paused = v; },
      speaking: false,
      cancel() { cancelled++; },
      resume() { resumed++; paused = false; },
      speak(u: SpeechSynthesisUtterance) { calls.push(u.text); },
      getVoices() { return []; },
      onvoiceschanged: null,
    };
    Object.defineProperty(window, "speechSynthesis", { value: mock, configurable: true });
    Object.assign(window, { __speechMock: mock, __speechCalls: calls, __speechCancelCount: () => cancelled, __speechResumeCount: () => resumed });
  });
}

test.describe("Speech callouts — mobile app", () => {
  test.use({ userAgent: "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36", viewport: { width: 390, height: 844 } });

  test.beforeEach(async ({ page }) => {
    await installSpeechMock(page);
    await page.addInitScript(() => {
      localStorage.setItem("airwsjtx.mobile.settings", JSON.stringify({
        myCall: "AD7NP", myGrid: "CN87", dxCall: "", dxGrid: "", speechEnabled: true,
      }));
    });
  });

  test("speech checkbox is shown in settings", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(page.getByText("Speech callouts")).toBeVisible();
  });

  test("speech checkbox is checked when speechEnabled is true", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const checkbox = page.locator('input[type="checkbox"]').filter({ hasText: "" }).first();
    // The speech checkbox should be checked
    const speechLabel = page.getByText("Speech callouts").locator("..");
    const cb = speechLabel.locator('input[type="checkbox"]');
    await expect(cb).toBeChecked();
  });

  test("no speech is fired when speechEnabled is false", async ({ page }) => {
    await page.addInitScript(() => {
      localStorage.setItem("airwsjtx.mobile.settings", JSON.stringify({
        myCall: "AD7NP", myGrid: "CN87", dxCall: "", dxGrid: "", speechEnabled: false,
      }));
    });
    await page.goto("/");
    await expect(page.getByRole("button", { name: "Settings", exact: true })).toBeVisible();
    const callCount = await page.evaluate(() => (window as unknown as { __speechCalls: string[] }).__speechCalls.length);
    expect(callCount).toBe(0);
  });

  test("speechSynthesis is present in this environment", async ({ page }) => {
    await page.goto("/");
    const supported = await page.evaluate(() => "speechSynthesis" in window);
    expect(supported).toBe(true);
  });
});

test.describe("Speech callouts — rotator panel", () => {
  test.beforeEach(async ({ page }) => {
    await installSpeechMock(page);
    // Start with rotator speech enabled
    await page.addInitScript(() => {
      localStorage.setItem("airwsjtx.rotatorSpeech.enabled", "1");
      localStorage.setItem("airwsjtx.rotatorSpeech.thresholdDeg", "3");
      localStorage.setItem("airwsjtx.rotatorSpeech.intervalSec", "2");
    });
  });

  test("speech checkbox is shown in rotator panel before rotator connection", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByText("Speech callouts").first()).toBeVisible();
    await expect(page.getByRole("button", { name: /Speech on/i })).toBeVisible();
  });

  test("quick speech toggle can disable and re-enable announcements", async ({ page }) => {
    await page.goto("/");
    const toggle = page.getByRole("button", { name: /Speech on/i });
    await toggle.click();
    await expect(page.getByRole("button", { name: /Speech off/i })).toBeVisible();
    const speechLabel = page.getByText("Speech callouts").locator("..");
    await expect(speechLabel.locator('input[type="checkbox"]')).not.toBeChecked();
    await page.getByRole("button", { name: /Speech off/i }).click();
    await expect(page.getByRole("button", { name: /Speech on/i })).toBeVisible();
  });

  test("hovering the speech button does not announce position while the rotor is off", async ({ page }) => {
    await page.goto("/");
    const toggle = page.getByRole("button", { name: /Speech on/i });
    await toggle.hover();
    await page.waitForTimeout(500);
    const calls = await page.evaluate(() => (window as unknown as { __speechCalls: string[] }).__speechCalls);
    expect(calls).toEqual([]);
  });

  test("manual compass click announces rotor off when the rotor is not connected", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Manual", exact: true }).click();
    await page.locator('svg[width="170"][height="170"]').first().click({ position: { x: 85, y: 10 } });
    await expect.poll(async () =>
      page.evaluate(() => (window as unknown as { __speechCalls: string[] }).__speechCalls[0] ?? "")
    ).toBe("Rotor off");
  });

  test("manual mode ignores locked aircraft updates", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Manual", exact: true }).click();
    await page.evaluate(() => {
      const emit = (window as unknown as Record<string, (e: string, p: string) => void>).__tauriMockEmit;
      if (!emit) return;
      emit("sidecar-message", JSON.stringify({
        event: "plane_locked",
        data: {
          icao24: "abc123",
          callsign: "TEST123",
          altitude_ft: 35000,
          az_a: 63.2,
          el_a: 1.4,
          az_b: 180,
          el_b: 5,
          scatter_score: 0.42,
          off_path_deg: 2,
          state: "plane_locked",
        },
      }));
    });
    await page.waitForTimeout(500);
    const calls = await page.evaluate(() => (window as unknown as { __speechCalls: string[] }).__speechCalls);
    expect(calls).toEqual([]);
  });

  test("speech synthesis cancel is called before each speak", async ({ page }) => {
    await page.goto("/");
    // Simulate rotator connected + movement beyond threshold
    await page.evaluate(() => {
      const emit = (window as unknown as Record<string, (e: string, p: string) => void>).__tauriMockEmit;
      if (!emit) return;
      emit("sidecar-message", JSON.stringify({ event: "rotator_connected", data: { host: "127.0.0.1", port: 4533 } }));
      // First position (establishes baseline, no speak)
      emit("sidecar-message", JSON.stringify({ event: "rotator_position", data: { azimuth: 90, elevation: 45 } }));
    });
    // Wait for state to settle
    await page.waitForTimeout(100);
    // Move by more than threshold + interval
    await page.evaluate(() => {
      const emit = (window as unknown as Record<string, (e: string, p: string) => void>).__tauriMockEmit;
      if (!emit) return;
      emit("sidecar-message", JSON.stringify({ event: "rotator_position", data: { azimuth: 100, elevation: 50 } }));
    });
    await page.waitForTimeout(2200); // exceed 2s interval
    await page.evaluate(() => {
      const emit = (window as unknown as Record<string, (e: string, p: string) => void>).__tauriMockEmit;
      if (!emit) return;
      emit("sidecar-message", JSON.stringify({ event: "rotator_position", data: { azimuth: 115, elevation: 55 } }));
    });
    await page.waitForTimeout(200);
    const cancelCount = await page.evaluate(() =>
      (window as unknown as { __speechCancelCount: () => number }).__speechCancelCount()
    );
    expect(cancelCount).toBeGreaterThan(0);
  });
});

test.describe("Speech callouts — graceful degradation", () => {
  test.use({ userAgent: "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36", viewport: { width: 390, height: 844 } });

  test("shows (unavailable) when speechSynthesis is absent", async ({ page }) => {
    // Remove speechSynthesis before page load
    await page.addInitScript(() => {
      Object.defineProperty(window, "speechSynthesis", { value: undefined, configurable: true });
      delete (window as unknown as Record<string, unknown>).speechSynthesis;
      localStorage.setItem("airwsjtx.mobile.settings", JSON.stringify({
        myCall: "AD7NP", myGrid: "CN87", dxCall: "", dxGrid: "", speechEnabled: false,
      }));
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    await expect(page.getByText("(unavailable)")).toBeVisible();
  });

  test("speech checkbox is disabled when speechSynthesis is absent", async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window, "speechSynthesis", { value: undefined, configurable: true });
      delete (window as unknown as Record<string, unknown>).speechSynthesis;
      localStorage.setItem("airwsjtx.mobile.settings", JSON.stringify({
        myCall: "AD7NP", myGrid: "CN87", dxCall: "", dxGrid: "", speechEnabled: false,
      }));
    });
    await page.goto("/");
    await page.getByRole("button", { name: "Settings", exact: true }).click();
    const speechLabel = page.getByText("Speech callouts").locator("..");
    const cb = speechLabel.locator('input[type="checkbox"]');
    await expect(cb).toBeDisabled();
  });
});

test.describe("Speech callouts — Linux native fallback", () => {
  test.use({ userAgent: "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36" });

  test.beforeEach(async ({ page }) => {
    await page.addInitScript(() => {
      Object.defineProperty(window, "speechSynthesis", { value: undefined, configurable: true });
      delete (window as unknown as Record<string, unknown>).speechSynthesis;
      Object.assign(window, { __TAURI_INTERNALS__: {} });
      localStorage.setItem("airwsjtx.rotatorSpeech.enabled", "1");
      localStorage.setItem("airwsjtx.rotatorSpeech.intervalSec", "2");
    });
  });

  for (const error of [
    "No speech engine found. Install speech-dispatcher or espeak-ng.",
    "Cannot start espeak-ng: Permission denied",
    "spd-say failed (exit status: 1). Check the speech engine and audio configuration.",
  ]) {
    test(`logs native speech failure: ${error}`, async ({ page }) => {
      const errors: string[] = [];
      page.on("pageerror", e => errors.push(e.message));
      await page.addInitScript(message => Object.assign(window, { __mockSpeechError: message }), error);
      await page.goto("/");
      await page.getByRole("button", { name: "Manual", exact: true }).click();
      await page.locator('svg[width="170"][height="170"]').first().click({ position: { x: 85, y: 10 } });
      await expect(page.getByText(`Speech: ${error}`, { exact: false })).toBeVisible();
      expect(errors).toEqual([]);
    });
  }

  test("uses native pacing and reports cancellation errors in the app log", async ({ page }) => {
    await page.addInitScript(() => Object.assign(window, { __mockSpeechStopError: "Cannot stop espeak-ng: access denied" }));
    await page.goto("/");
    await page.getByRole("button", { name: "Manual", exact: true }).click();
    await page.evaluate(() => {
      const emit = (window as unknown as Record<string, (e: string, p: string) => void>).__tauriMockEmit;
      emit("sidecar-message", JSON.stringify({ event: "rotator_connected", data: { host: "127.0.0.1", port: 4533 } }));
    });
    await expect(page.getByText("Rotator connected (127.0.0.1:4533)", { exact: false })).toBeVisible();
    await page.locator('svg[width="170"][height="170"]').first().click({ position: { x: 85, y: 10 } });
    await expect.poll(() => page.evaluate(() =>
      (window as unknown as { __mockSpeechRequests?: { cmd: string; text?: string }[] })
        .__mockSpeechRequests?.find(request => request.cmd === "speak_native")?.text
    )).toMatch(/^Azimuth, -?\d+, Elevation, -?\d+$/);
    await page.getByRole("button", { name: /Speech on/i }).click();
    await expect(page.getByText("Speech: Cannot stop espeak-ng: access denied", { exact: false })).toBeVisible();
  });

  test("turning speech off cancels a rotor-off callout too", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "Manual", exact: true }).click();
    await page.locator('svg[width="170"][height="170"]').first().click({ position: { x: 85, y: 10 } });
    await expect.poll(() => page.evaluate(() =>
      (window as unknown as { __mockSpeechRequests?: { text?: string }[] })
        .__mockSpeechRequests?.some(request => request.text === "Rotor off")
    )).toBe(true);
    await page.getByRole("button", { name: /Speech on/i }).click();
    await expect.poll(() => page.evaluate(() =>
      (window as unknown as { __mockSpeechRequests?: { cmd: string }[] })
        .__mockSpeechRequests?.filter(request => request.cmd === "stop_native_speech").length
    )).toBe(1);
  });

  test("prefers the existing Web Speech API when present", async ({ page }) => {
    await installSpeechMock(page);
    await page.goto("/");
    await page.getByRole("button", { name: "Manual", exact: true }).click();
    await page.locator('svg[width="170"][height="170"]').first().click({ position: { x: 85, y: 10 } });
    await expect.poll(() => page.evaluate(() =>
      (window as unknown as { __speechCalls: string[] }).__speechCalls
    )).toEqual(["Rotor off"]);
    expect(await page.evaluate(() =>
      (window as unknown as { __mockSpeechRequests?: unknown[] }).__mockSpeechRequests ?? []
    )).toEqual([]);
  });
});
