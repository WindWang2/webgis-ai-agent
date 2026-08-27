// PROTOTYPE (throwaway) — esbuild bundler for the PoC worker.
// Bundles geolibre-wasm/tools (incl. bare import @bjorn3/browser_wasi_shim)
// into a self-contained ESM worker; .wasm files are emitted as assets and
// new URL(..., import.meta.url) references are rewritten by esbuild.
import * as esbuild from "esbuild";

const ctx = await esbuild.context({
  entryPoints: ["src/worker.js", "src/main.js"],
  bundle: true,
  format: "esm",
  target: "esnext",
  outdir: "dist",
  loader: { ".wasm": "file" },
  metafile: true,
  logLevel: "info",
});
const r = await ctx.rebuild();
await ctx.dispose();

// esbuild 未重写 tools.mjs 里的 new URL("./geolibre-cli.wasm", import.meta.url)
// 资产引用（保持运行时相对解析）——直接把 wasm 拷到 dist/ 旁，让 URL 原样命中。
import { copyFileSync, statSync } from "node:fs";
const wasmFiles = ["geolibre-cli.wasm", "geolibre_wasm_bg.wasm"];
for (const f of wasmFiles) {
  copyFileSync(`node_modules/geolibre-wasm/${f}`, `dist/${f}`);
  console.log(`dist/${f}: ${(statSync(`dist/${f}`).size / 1024 / 1024).toFixed(2)} MiB`);
}
