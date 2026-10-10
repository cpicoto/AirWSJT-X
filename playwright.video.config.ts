import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  testMatch: "demo-video.spec.ts",
  timeout: 120000,
  use: {
    baseURL: "http://localhost:1421",
    viewport: { width: 1440, height: 900 },
    video: { mode: "on", size: { width: 1440, height: 900 } },
  },
  webServer: {
    command: "npx vite --config vite.screenshot.config.ts",
    port: 1421,
    reuseExistingServer: true,
    timeout: 30000,
  },
});
