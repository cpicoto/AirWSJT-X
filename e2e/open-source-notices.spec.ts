import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";

const revision = execFileSync("git", ["rev-parse", "HEAD"], { encoding: "utf8" }).trim();

for (const mobile of [false, true]) {
  test(`complete offline open-source notices on ${mobile ? "mobile" : "desktop"}`, async ({ page }) => {
    if (mobile) {
      await page.addInitScript(() => {
        Object.defineProperty(navigator, "userAgent", { value: "Android" });
      });
    }
    await page.goto("/");
    if (mobile) await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.context().setOffline(true);
    await page.getByRole("button", { name: "Open-source notices", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await expect(dialog).toBeVisible();
    await expect(dialog).toContainText("Oliver Jowett");
    await expect(dialog).toContainText("Salvatore Sanfilippo");
    await expect(dialog).toContainText("FlightAware LLC");
    await expect(dialog.getByRole("link", { name: /\/archive\// })).toHaveAttribute(
      "href", `https://github.com/cpicoto/AirWSJT-X/archive/${revision}.tar.gz`,
    );
    for (const [title, file] of [
      ["dump1090 copyrights, BSD terms and adaptation details", "scripts/dump1090-fa/UPSTREAM.txt"],
      ["GNU GPL v2 (upstream dump1090)", "scripts/dump1090-fa/COPYING"],
      ["GNU GPL v3 (AirWSJT-X combined work)", "LICENSE"],
    ]) {
      const section = dialog.locator("details").filter({ has: page.locator("summary", { hasText: title }) });
      await section.locator("summary").click();
      await expect(section.locator("pre")).toBeVisible();
      expect(await section.locator("pre").textContent()).toBe(readFileSync(file, "utf8"));
    }
    await page.keyboard.press("Escape");
    await expect(dialog).not.toBeVisible();
    await page.getByRole("button", { name: "Open-source notices", exact: true }).click();
    await dialog.getByRole("button", { name: "Close open-source notices" }).click();
    await expect(dialog).not.toBeVisible();
  });

  test(`source links open externally with visible failures on ${mobile ? "mobile" : "desktop"}`, async ({ page }) => {
    await page.addInitScript((isMobile) => {
      if (isMobile) Object.defineProperty(navigator, "userAgent", { value: "Android" });
      Object.defineProperty(window, "__TAURI_INTERNALS__", { value: {} });
      window.addEventListener("mock-shell-open", event => {
        document.documentElement.dataset.openedUrl = (event as CustomEvent<string>).detail;
      });
    }, mobile);
    await page.goto("/");
    if (mobile) await page.getByRole("button", { name: "Settings", exact: true }).click();
    await page.getByRole("button", { name: "Open-source notices", exact: true }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("link", { name: /\/archive\// }).click();
    await expect(page.locator("html")).toHaveAttribute(
      "data-opened-url", `https://github.com/cpicoto/AirWSJT-X/archive/${revision}.tar.gz`,
    );
    await page.evaluate(() => Object.defineProperty(window, "__tauriMockShellFailure", { value: true }));
    await dialog.getByRole("link", { name: /\/archive\// }).click();
    await expect(dialog.getByRole("alert")).toContainText("Browser unavailable");
    await expect(dialog.getByRole("alert")).toContainText("Copy the displayed URL");
  });
}
