// Vite config for the two outputs of one frontend (docs/product_design.md section 13, app/CONTRACT.md section 7).
// `vite build` writes dist/ for the local app (relative base, served by the backend).
// `vite build --mode single` writes dist-single/index.html with every script, style and asset inlined and no data;
// the bundle builder (app/build/) injects the data parts after this build.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

export default defineConfig(({ mode }) => {
  const single = mode === "single";
  return {
    base: "./",
    plugins: single ? [react(), viteSingleFile({ removeViteModuleLoader: true })] : [react()],
    define: {
      // Single-file pages have no server: the adapter chooser still prefers an embedded bundle when present.
      __SCS_SINGLE__: JSON.stringify(single),
    },
    build: {
      outDir: single ? "dist-single" : "dist",
      emptyOutDir: true,
      target: "es2020",
      // Inline every asset (Leaflet's control images) so nothing is fetched at run time.
      assetsInlineLimit: single ? 100_000_000 : 8192,
      cssCodeSplit: false,
      sourcemap: false,
      chunkSizeWarningLimit: 4000,
      rollupOptions: single ? { output: { inlineDynamicImports: true } } : {},
    },
    server: {
      host: "127.0.0.1",
      port: 5173,
      proxy: { "/api": "http://127.0.0.1:8750" },
    },
  };
});
