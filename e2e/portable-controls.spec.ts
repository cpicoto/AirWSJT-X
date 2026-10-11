import { test, expect } from "@playwright/test";

test("Az/El controls remain available when hidden and remember mode and scale", async ({ page }) => {
  await page.goto("/");
  const controls = page.getByRole("group", { name: "Az / El display controls" });
  await controls.getByRole("button", { name: "Hide", exact: true }).click();
  await expect(page.locator(".portable-panel, .portable-overlay")).toHaveCount(0);
  await expect(controls).toBeVisible();
  const scale = controls.getByRole("slider", { name: "Scale" });
  await scale.fill("150");
  await controls.getByRole("button", { name: "Overlay", exact: true }).click();
  await expect(page.locator(".portable-overlay")).toBeVisible();
  await expect(page.locator(".portable-grid")).toHaveCSS("font-size", "108px");
  await controls.getByRole("button", { name: "Hide", exact: true }).click();
  await page.reload();
  await expect(controls).toBeVisible();
  await expect(scale).toHaveValue("150");
  await expect(controls.getByRole("button", { name: "Hide", exact: true })).toHaveAttribute("aria-pressed", "true");
  await controls.getByRole("button", { name: "Panel", exact: true }).click();
  await expect(page.locator(".portable-panel")).toBeVisible();
  await expect(page.locator(".portable-grid")).toHaveCSS("font-size", "108px");
});
