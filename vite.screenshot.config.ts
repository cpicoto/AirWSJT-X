import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";
import { sourceRevision } from "./vite.config.ts";

export default defineConfig({
  plugins: [react()],
  define: { __AIRWSJTX_SOURCE_REVISION__: JSON.stringify(sourceRevision) },
  clearScreen: false,
  server: {
    port: 1421,
    strictPort: true,
  },
  resolve: {
    alias: {
      "@tauri-apps/api/core": path.resolve(__dirname, "e2e/mocks/tauri-core.ts"),
      "@tauri-apps/api/event": path.resolve(__dirname, "e2e/mocks/tauri-event.ts"),
      "@tauri-apps/plugin-shell": path.resolve(__dirname, "e2e/mocks/tauri-shell.ts"),
    },
  },
});
