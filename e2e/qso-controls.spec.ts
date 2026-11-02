import { expect, test } from "@playwright/test";

test("QSO control requires 2 m or higher and disabled WSJT-X Tx", async ({ page }) => {
  await page.addInitScript(() => Object.assign(window, { __mockState: "ready" }));
  await page.goto("/");
  const start = page.getByRole("button", { name: "▶ Start QSO" });
  await expect(start).toBeDisabled();
  await expect(start).toHaveAttribute("title", /Connect WSJT-X/);
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { __mockRpcRequests?: { method: string }[] }).__mockRpcRequests
      ?.some(request => request.method === "get_status")
  )).toBe(true);
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { __mockRpcRequests?: { method: string }[] }).__mockRpcRequests
      ?.filter(request => request.method === "wsjtx_request_status").length ?? 0
  )).toBeGreaterThanOrEqual(1);

  const emitStatus = async (freq_mhz: number, tx_enabled = false) => page.evaluate(({ freq_mhz, tx_enabled }) => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "wsjtx_status",
      data: {
        freq_mhz, mode: "Q65", sub_mode: "C", de_call: "K7MDL", de_grid: "CN87",
        dx_call: "W7PUA", dx_grid: "CN84", tx_enabled, transmitting: false, decoding: true, tr_period: 15,
      },
    }));
  }, { freq_mhz, tx_enabled });

  await page.evaluate(() => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({
      event: "plane_locked",
      data: { icao24: "abc123", callsign: "TEST1", altitude_ft: 35000, az_a: 90, el_a: 5,
        az_b: 180, el_b: 5, scatter_score: 0.8, off_path_deg: 2, state: "ready" },
    }));
  });

  await emitStatus(143.999999);
  await expect(start).toBeDisabled();
  await expect(start).toHaveAttribute("title", /144 MHz/);
  await emitStatus(144, true);
  await expect(start).toBeDisabled();
  await expect(start).toHaveAttribute("title", /Disable WSJT-X Tx/);
  for (const frequency of [144, 144.18, 432.15, 1296.15]) {
    await emitStatus(frequency);
    await expect(start).toBeEnabled();
  }
  await page.evaluate(() => {
    const emit = (window as unknown as Record<string, (event: string, payload: string) => void>).__tauriMockEmit;
    emit("sidecar-message", JSON.stringify({ event: "wsjtx_disconnected", data: {} }));
    emit("sidecar-message", JSON.stringify({ event: "wsjtx_connected", data: { app_id: "WSJT-X" } }));
  });
  await expect(start).toBeDisabled();
  await expect(start).toHaveAttribute("title", /Wait for WSJT-X status/);
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { __mockRpcRequests?: { method: string }[] }).__mockRpcRequests
      ?.filter(request => request.method === "wsjtx_request_status").length ?? 0
  )).toBeGreaterThanOrEqual(2);
  await emitStatus(1296.15);
  await expect(start).toBeEnabled();
  await expect(start).toHaveAttribute("title", /WSJT-X build with StartDxTx support/);
  await expect(page.getByRole("note")).toHaveText("Check WSJT-X Tx even/1st");
  await start.click();
  await expect.poll(() => page.evaluate(() =>
    (window as unknown as { __mockRpcRequests?: { method: string; params: Record<string, unknown> }[] })
      .__mockRpcRequests?.find(request => request.method === "start_qso")?.params
  )).toEqual({ dx_call: "W7PUA", dx_grid: "CN84" });
  await expect(page.getByRole("status", { name: /Waiting for WSJT-X/ })).toContainText("Waiting for WSJT-X");
});
