// Vite config for the two outputs of one frontend (docs/product_design.md section 13, app/CONTRACT.md section 7).
// `vite build` writes dist/ for the local app (relative base, served by the backend).
// `vite build --mode single` writes dist-single/index.html with every script, style and asset inlined and no data;
// the bundle builder (app/build/) injects the data parts after this build.
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { viteSingleFile } from "vite-plugin-singlefile";

// The icons package's two built-in loaders import the SVG paths of all 2,121 icons at both sizes (about 0.6 MB once
// inlined). The product registers its own loader with only the icons it names (src/theme/icons.ts), so both
// built-in loader modules are replaced here by a stub that is never called.
const ICON_LOADER_STUB = "\0scs-icon-loader-stub";
function iconLoaderStub() {
  return {
    name: "scs-icon-loader-stub",
    enforce: "pre" as const,
    resolveId(source: string, importer?: string) {
      if (importer && importer.includes("@blueprintjs/icons") && /paths-loaders\/(allPathsLoader|splitPathsBySizeLoader)(\.js)?$/.test(source)) return ICON_LOADER_STUB;
      return null;
    },
    load(id: string) {
      if (id !== ICON_LOADER_STUB) return null;
      return "const none = async () => { throw new Error('built-in icon loader removed; see src/theme/icons.ts'); };\nexport const allPathsLoader = none;\nexport const splitPathsBySizeLoader = none;\n";
    },
  };
}

export default defineConfig(({ mode }) => {
  const single = mode === "single";
  return {
    base: "./",
    plugins: single ? [iconLoaderStub(), react(), viteSingleFile({ removeViteModuleLoader: true })] : [iconLoaderStub(), react()],
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
