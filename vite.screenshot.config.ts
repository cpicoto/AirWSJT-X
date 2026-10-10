import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  server: {
    port: 1421,
    strictPort: true,
  },
  resolve: {
    alias: {
      "@tauri-apps/api/core": path.resolve(__dirname, "e2e/mocks/tauri-core.ts"),
      "@tauri-apps/api/event": path.resolve(__dirname, "e2e/mocks/tauri-event.ts"),
    },
  },
});
