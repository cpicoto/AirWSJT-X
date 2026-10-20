import { test, expect } from "@playwright/test";

test("Az/El controls remain available when hidden and remember mode", async ({ page }) => {
  await page.goto("/");
  const controls = page.getByRole("group", { name: "Az / El display controls" });
  // The docked Az/El panel is disabled; Show opens the floating readout.
  await expect(controls.getByRole("button", { name: "Panel", exact: true })).toHaveCount(0);
  await expect(controls.getByRole("button", { name: "Open", exact: true })).toHaveAttribute("aria-pressed", "false");
  await expect(page.locator(".portable-overlay")).toHaveCount(0);
  await controls.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page.locator(".portable-overlay")).toBeVisible();
  await controls.getByRole("button", { name: "Close", exact: true }).click();
  await expect(page.locator(".portable-panel, .portable-overlay")).toHaveCount(0);
  await expect(controls).toBeVisible();
  await expect(controls.getByRole("slider")).toHaveCount(0);
  await page.reload();
  await expect(controls.getByRole("button", { name: "Open", exact: true })).toHaveAttribute("aria-pressed", "false");
  await controls.getByRole("button", { name: "Open", exact: true }).click();
  await expect(page.locator(".portable-overlay")).toBeVisible();
});

test("Overlay text scales with the resizable frame and the frame can be dragged", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("group", { name: "Az / El display controls" }).getByRole("button", { name: "Open", exact: true }).click();
  const frame = page.locator(".overlay-frame");
  await expect(frame).toBeVisible();
  const fontPx = async () => parseFloat(await page.locator(".portable-overlay .portable-value").first().evaluate(el => getComputedStyle(el).fontSize));
  const before = await fontPx();
  await frame.evaluate(el => { (el as HTMLElement).style.width = "900px"; (el as HTMLElement).style.height = "300px"; });
  await expect.poll(fontPx).toBeGreaterThan(before + 10);
  await frame.evaluate(el => { (el as HTMLElement).style.width = "300px"; (el as HTMLElement).style.height = "140px"; });
  await expect.poll(fontPx).toBeLessThan(before - 10);

  const header = page.locator(".overlay-drag");
  const box = (await frame.boundingBox())!;
  const h = (await header.boundingBox())!;
  await page.mouse.move(h.x + 10, h.y + h.height / 2);
  await page.mouse.down();
  await page.mouse.move(h.x - 90, h.y + h.height / 2 + 60, { steps: 6 });
  await page.mouse.up();
  const after = (await frame.boundingBox())!;
  expect(after.x).toBeLessThan(box.x - 50);
  expect(after.y).toBeGreaterThan(box.y + 40);
});

