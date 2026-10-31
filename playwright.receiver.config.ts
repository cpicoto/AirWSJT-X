import { defineConfig } from "@playwright/test";

// Separate from the user's running desktop/dev preview.
export default defineConfig({
  testDir: "e2e",
  testMatch: ["local-receiver.spec.ts", "mobile-accounts.spec.ts", "candidate-layout.spec.ts", "aircraft-ranking.spec.ts"],
  timeout: 30000,
  use: {
    baseURL: "http://localhost:1429",
    viewport: { width: 1440, height: 900 },
  },
  webServer: {
    command: "npx vite --config vite.screenshot.config.ts --port 1429",
    port: 1429,
    reuseExistingServer: false,
    timeout: 30000,
  },
});
