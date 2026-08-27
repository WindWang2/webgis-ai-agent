// PROTOTYPE (throwaway) — wayfinder ticket-007 physics PoC worker.
// Runs Whitebox WASM tools (geolibre-wasm/tools) off the main thread:
// wasi.start() is synchronous/blocking, so a dedicated worker is mandatory.
const handlers = {
  async init() {
    const { initTools, listTools } = await import("geolibre-wasm/tools");
    const t = performance.now();
    await initTools(); // compileStreaming(geolibre-cli.wasm)
    const initMs = performance.now() - t;
    const tools = await listTools();
    // Resource timing for the wasm fetch lives in THIS worker's timeline.
    const res = performance
      .getEntriesByType("resource")
      .filter((e) => e.name.includes(".wasm"))
      .map((e) => ({
        name: e.name.split("/").pop(),
        transferSize: e.transferSize, // bytes over the wire (gzip)
        decodedBodySize: e.decodedBodySize,
      }));
    return { initMs, toolCount: tools.length, wasmResources: res };
  },

  async manifests({ tools: wanted }) {
    const { listManifests } = await import("geolibre-wasm/tools");
    const all = await listManifests();
    const arr = Array.isArray(all) ? all : Object.values(all);
    const picked = arr.filter((m) =>
      wanted.some((w) => m.id === w || m.name === w || m.tool === w),
    );
    return { manifests: picked.length ? picked : arr.slice(0, 2), total: arr.length };
  },

  async run({ tool, args, input }) {
    const { initTools, runTool } = await import("geolibre-wasm/tools");
    await initTools();
    const inBytes = Object.values(input).reduce((a, b) => a + b.byteLength, 0);
    const t = performance.now();
    const res = await runTool(tool, { args, input });
    const runMs = performance.now() - t;
    const files = {};
    let outBytes = 0;
    for (const [k, v] of Object.entries(res.files)) {
      files[k] = v.buffer.slice(v.byteOffset, v.byteOffset + v.byteLength);
      outBytes += v.byteLength;
    }
    return {
      exitCode: res.exitCode,
      stdout: res.stdout.slice(-15),
      files,
      runMs,
      inBytes,
      outBytes,
    };
  },
};

self.onmessage = async (e) => {
  const { id, op, payload } = e.data;
  try {
    const result = await handlers[op](payload);
    const transfers = [];
    if (result?.files)
      for (const b of Object.values(result.files)) transfers.push(b);
    self.postMessage({ id, ok: true, result }, transfers);
  } catch (err) {
    self.postMessage({ id, ok: false, error: String(err?.stack || err) });
  }
};
