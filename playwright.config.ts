import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "e2e",
  testMatch: ["screenshots.spec.ts", "rig-filter.spec.ts", "portable-controls.spec.ts", "candidate-layout.spec.ts", "mobile-accounts.spec.ts", "speech-callouts.spec.ts", "compass.spec.ts"],
  timeout: 30000,
  use: {
    baseURL: "http://localhost:1421",
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: "npx vite --config vite.screenshot.config.ts",
    port: 1421,
    reuseExistingServer: true,
    timeout: 30000,
  },
});
