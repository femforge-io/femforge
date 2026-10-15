import { resolve } from "node:path";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import { BUNDLE, LICENSES_JSON, SERVE_DIR } from "./scripts/paths.mts";
import { dist } from "./scripts/vite-plugin-dist.mts";
import { monacoWorkers } from "./scripts/vite-plugin-monaco.mts";

const REACT_EXTERNAL = /^react$|^react-dom(\/.*)?$/;
const WATCHING = process.argv.includes("--watch");
const src = (path: string): string => resolve(import.meta.dirname, "src", path);

export default defineConfig({
  base: "/__ff/",
  plugins: [react(), tailwindcss(), monacoWorkers(), dist()],
  define: {
    "process.env.NODE_ENV": JSON.stringify("production"),
    // plotly.js's source build (lib/core) reaches for node's `global`, which the dist bundle had already resolved.
    global: "globalThis",
  },
  resolve: {
    alias: [
      // CommonJS shims whose require("react") would survive a UMD build with react external.
      { find: /^use-sync-external-store\/shim\/with-selector(\.js)?$/, replacement: src("shims/with-selector.js") },
      { find: /^use-sync-external-store\/shim(\/index\.js)?$/, replacement: src("shims/use-sync-external-store.js") },
      // plotly's registry.js requires maplibre's stylesheet at module scope, under a branch no trace of ours takes;
      // the bundler resolves it anyway and extracts 70 kB of dead CSS.
      { find: "maplibre-gl/dist/maplibre-gl.css", replacement: src("shims/maplibre-gl.css") },
      // shadcn 4.21 writes `from "cn"` for the utils alias.
      { find: /^cn$/, replacement: src("lib/utils.ts") },
      { find: "@", replacement: src("") },
    ],
  },
  build: {
    lib: {
      entry: "src/index.tsx",
      name: "Ff",
      formats: ["umd"],
      fileName: () => BUNDLE,
      cssFileName: "ff",
    },
    outDir: SERVE_DIR,
    // A watch build keeps props.json and the last complete dist in place between rebuilds.
    emptyOutDir: !WATCHING,
    copyPublicDir: true,
    minify: true,
    license: { fileName: LICENSES_JSON },
    rollupOptions: {
      external: (id: string) => REACT_EXTERNAL.test(id),
      output: {
        globals: (id: string) => (id.startsWith("react-dom") ? "ReactDOM" : "React"),
      },
    },
  },
});
