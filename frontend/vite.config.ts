import { resolve } from "node:path";
import { defineConfig } from "vite";
import { gzipSync } from "node:zlib";
import react from "@vitejs/plugin-react";
import { readFileSync, readdirSync, writeFileSync } from "node:fs";
export default defineConfig({
  plugins: [
    react(),
    {
      name: "public-asset-precompression",
      writeBundle(options, bundle) {
        // Read final files: Vite's preload rewriting occurs after generateBundle.
        for (const name of Object.keys(bundle)) {
          if (!/\.(?:js|mjs|css|svg|json|txt)$/.test(name)) continue;
          const path = resolve(options.dir!, name), source = readFileSync(path);
          if (source.length >= 1024) writeFileSync(path+".gz", gzipSync(source,{level:9}));
        }
      },
    },
    {
      name: "runtime-license-notices",
      generateBundle() {
        for (const dir of ["cmaps", "standard_fonts", "wasm"]) {
          for (const file of readdirSync(`node_modules/pdfjs-dist/${dir}`)) {
            this.emitFile({
              type: "asset",
              fileName: `pdfjs/${dir}/${file}`,
              source: readFileSync(`node_modules/pdfjs-dist/${dir}/${file}`),
            });
          }
        }
        const packages = [
          "react",
          "react-dom",
          "scheduler",
          "vite",
          "pdfjs-dist",
          "lucide-react",
          "dompurify",
          "marked",
          "katex",
        ];
        this.emitFile({
          type: "asset",
          fileName: "THIRD_PARTY_NOTICES.txt",
          source: packages
            .map(
              (name) =>
                `${name}\n${readFileSync(`node_modules/${name}/${name === "vite" ? "LICENSE.md" : "LICENSE"}`, "utf8")}\n`,
            )
            .join("\n"),
        });
      },
    },
  ],
  base: "/static/workbench/",
  build: {
    outDir: "../static/workbench",
    emptyOutDir: true,
    manifest: true,
    rolldownOptions: { input: "src/main.tsx" },
  },
});
