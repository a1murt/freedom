import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";
import path from "node:path";

// `pnpm build`        -> dist/            (multi-file, served by nginx in Docker)
// `pnpm build:single` -> dist-single/index.html (one HTML file for a Dynamics 365 web resource)
export default defineConfig(({ mode }) => {
  const single = mode === "single";
  return {
    plugins: [react(), ...(single ? [viteSingleFile({ removeViteModuleLoader: true })] : [])],
    resolve: { alias: { "@": path.resolve(__dirname, "src") } },
    server: {
      port: 5173,
      proxy: {
        "/api": { target: "http://localhost:8000", changeOrigin: true },
      },
    },
    build: {
      outDir: single ? "dist-single" : "dist",
      emptyOutDir: true,
      sourcemap: false,
      chunkSizeWarningLimit: 2000,
      ...(single
        ? { cssCodeSplit: false, assetsInlineLimit: 100_000_000, rollupOptions: { output: { inlineDynamicImports: true } } }
        : {}),
    },
  };
});
