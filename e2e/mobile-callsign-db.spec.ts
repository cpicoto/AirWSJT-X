import { test, expect } from "@playwright/test";

const database = {
  version: "2026-11-06.1",
  entries: {
    AD7NP: { grid: "CN87VP" },
    K7MDL: { grid: "CN87XS" },
    KD7UO: { grid: "CN88QK" },
    VE7NX: { grid: "CN89JI" },
    W7GLF: { grid: "CN87VR" },
    W7PUA: { grid: "CN84JQ" },
  },
};

test.use({
  userAgent: "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15",
  viewport: { width: 390, height: 844 },
});

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.mobile.settings", JSON.stringify({
      myCall: "AD7NP", myGrid: "CN87VP", dxCall: "", dxGrid: "", speechEnabled: false,
    }));
  });
  await page.route("**/airwsjtx/callsign-grid-db.json", route => route.fulfill({
    status: 200,
    contentType: "application/json",
    body: JSON.stringify(database),
  }));
});

test("downloaded group stations are visible and can set the mobile DX endpoint", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();

  await expect(page.getByText("6 entries · fresh", { exact: false })).toBeVisible();
  await expect(page.getByLabel("DX Favorite").locator("option")).toHaveCount(7);

  await page.getByLabel("DX Favorite").selectOption("K7MDL");
  await expect(page.getByLabel("DX Callsign", { exact: true })).toHaveValue("K7MDL");
  await expect(page.getByLabel("DX Grid", { exact: true })).toHaveValue("CN87XS");
});

test("downloaded stations remain visible after reopening the app", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("DX Favorite").locator("option")).toHaveCount(7);

  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("DX Favorite").locator('option[value="VE7NX"]')).toHaveText("VE7NX — CN89JI");
});

test("cached favorites remain visible while a stale database refresh is pending", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("DX Favorite").locator("option")).toHaveCount(7);

  await page.evaluate(async () => {
    const db = await new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open("airwsjtx_callsign", 1);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction("callsign_meta", "readwrite");
      tx.objectStore("callsign_meta").put("2020-01-01T00:00:00.000Z", "downloaded_at");
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
  });
  await page.unroute("**/airwsjtx/callsign-grid-db.json");
  await page.route("**/airwsjtx/callsign-grid-db.json", async () => {
    await new Promise(() => {});
  });

  await page.reload();
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("DX Favorite").locator("option")).toHaveCount(7);
});

test("a manually changed grid does not prevent reapplying the same favorite", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await page.getByLabel("DX Favorite").selectOption("K7MDL");
  await page.getByLabel("DX Grid", { exact: true }).fill("CN87AA");
  await expect(page.getByLabel("DX Favorite")).toHaveValue("");
  await page.getByLabel("DX Favorite").selectOption("K7MDL");
  await expect(page.getByLabel("DX Grid", { exact: true })).toHaveValue("CN87XS");
});

test("GPS automatically generates My Grid for the mobile station", async ({ page, context }) => {
  await context.grantPermissions(["geolocation"]);
  await context.setGeolocation({ latitude: 47.631244, longitude: -122.21553, accuracy: 5 });
  await page.addInitScript(() => {
    localStorage.setItem("airwsjtx.mobile.settings", JSON.stringify({
      myCall: "AD7NP", myGrid: "", myGridSource: "gps", dxCall: "", dxGrid: "", speechEnabled: false,
    }));
  });

  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByLabel("My Grid", { exact: true })).toHaveValue("CN87VP");
  await expect(page.getByText("My Grid is updated automatically from GPS.", { exact: true })).toBeVisible();
});

test("GPS errors invalidate the current-location shortcut", async ({ page }) => {
  await page.addInitScript(() => {
    Object.defineProperty(navigator, "geolocation", {
      configurable: true,
      value: {
        watchPosition: (_success: PositionCallback, error: PositionErrorCallback) => {
          setTimeout(() => error({ code: 2, message: "Position unavailable", PERMISSION_DENIED: 1, POSITION_UNAVAILABLE: 2, TIMEOUT: 3 }), 0);
          return 1;
        },
        clearWatch: () => {},
      },
    });
  });
  await page.goto("/");
  await page.getByRole("button", { name: "Settings", exact: true }).click();
  await expect(page.getByRole("button", { name: "Waiting for Current Location…" })).toBeDisabled();
  await expect(page.getByRole("status")).toHaveText("Current location unavailable: Position unavailable");
});
