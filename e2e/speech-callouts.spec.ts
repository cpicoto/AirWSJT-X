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
    await expect(page.locator("span").getByText("MOBILE", { exact: true })).toBeVisible();
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
    await expect(page.locator("span").getByText("MOBILE", { exact: true })).toBeVisible();
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

  test("speech checkbox is shown in rotator panel when connected", async ({ page }) => {
    await page.addInitScript(() => {
      Object.assign(window, { __mockState: "tracking" });
    });
    await page.goto("/");
    // Simulate rotator connected event
    await page.evaluate(() => {
      const emit = (window as unknown as Record<string, (e: string, p: string) => void>).__tauriMockEmit;
      if (emit) {
        emit("sidecar-message", JSON.stringify({ event: "rotator_connected", data: { host: "127.0.0.1", port: 4533 } }));
        emit("sidecar-message", JSON.stringify({ event: "rotator_position", data: { azimuth: 90, elevation: 45 } }));
      }
    });
    await expect(page.getByText("Speech").first()).toBeVisible();
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
