import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

/// <reference types="vitest" />

// @ts-expect-error process is a nodejs global
const host = process.env.TAURI_DEV_HOST;

// https://vite.dev/config/
export default defineConfig(async () => ({
  plugins: [react()],
  test: {
    // Per-file environment overrides (e.g. // @vitest-environment jsdom) are respected.
    // The default environment remains 'node' for pure-function tests.
    environment: "node",
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          const moduleId = id.replace(/\\/g, "/");

          if (!moduleId.includes("/node_modules/")) return undefined;

          if (moduleId.includes("/@xyflow/") || moduleId.includes("/@dagrejs/")) {
            return "graph-vendor";
          }

          if (
            /\/(react-markdown|remark-|rehype-katex|katex|mdast-util-|micromark|hast-util-|unist-util-|vfile|property-information|space-separated-tokens|comma-separated-tokens)\//.test(
              moduleId,
            )
          ) {
            return "markdown-vendor";
          }

          if (moduleId.includes("/@tanstack/")) {
            return "query-vendor";
          }

          if (moduleId.includes("/@tauri-apps/")) {
            return "tauri-vendor";
          }

          if (moduleId.includes("/react-resizable-panels/")) {
            return "layout-vendor";
          }

          if (
            moduleId.includes("/react-router") ||
            moduleId.includes("/react-dom/") ||
            moduleId.includes("/react/")
          ) {
            return "react-vendor";
          }

          return undefined;
        },
      },
    },
  },

  // Vite options tailored for Tauri development and only applied in `tauri dev` or `tauri build`
  //
  // 1. prevent Vite from obscuring rust errors
  clearScreen: false,
  // 2. tauri expects a fixed port, fail if that port is not available
  server: {
    port: 1420,
    strictPort: true,
    host: host || false,
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    watch: {
      // 3. tell Vite to ignore watching `src-tauri`
      ignored: ["**/src-tauri/**"],
    },
  },
}));
