import { describe, it, expect, vi, afterEach } from "vitest";
import { diffSpecsAsync, _resetWorkerBridgeForTests } from "./worker-bridge";
import { diffSpecs } from "./reconciler";
import type { MapSpec } from "./types";

// diffSpecsAsync: worker-offloaded diff with a main-thread fallback.

const spec: MapSpec = {
  version: "1.0",
  sources: {
    A: { type: "geojson", inlineData: { type: "FeatureCollection", features: [] } },
  },
  layers: [{ id: "A__point", source: "A", type: "circle", paint: { "circle-radius": 6 } }],
};

describe("diffSpecsAsync", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    _resetWorkerBridgeForTests();
  });

  it("falls back to a synchronous diff when Worker is unavailable", async () => {
    vi.stubGlobal("Worker", undefined);
    const patch = await diffSpecsAsync(null, spec);
    expect(patch.layers).toHaveLength(1);
    expect(patch.layers[0].kind).toBe("add");
    expect(patch.sources[0].kind).toBe("add");
  });

  it("round-trips through a worker when one is available", async () => {
    let listener: ((event: { data: { id: string; patch: ReturnType<typeof diffSpecs> } }) => void) | null = null;
    class FakeWorker {
      constructor(_url: URL, _opts?: { type?: string }) {}
      postMessage(msg: { id: string; prev: MapSpec | null; next: MapSpec }) {
        const { id, prev, next } = msg;
        queueMicrotask(() => {
          const patch = diffSpecs(prev, next);
          listener?.({ data: { id, patch } });
        });
      }
      addEventListener(_type: string, cb: typeof listener) {
        listener = cb;
      }
      removeEventListener() {}
    }
    vi.stubGlobal("Worker", FakeWorker as unknown as typeof Worker);

    const patch = await diffSpecsAsync(null, spec);
    expect(patch.layers[0].kind).toBe("add");
    // The worker path must produce the same patch as the sync diff.
    expect(patch).toEqual(diffSpecs(null, spec));
  });

  it("resolves an empty patch on worker error, matching the no-op contract", async () => {
    let listener: ((event: { data: { id: string; patch: ReturnType<typeof diffSpecs>; error?: string } }) => void) | null = null;
    class ErrorWorker {
      constructor(_url: URL, _opts?: { type?: string }) {}
      postMessage(msg: { id: string }) {
        queueMicrotask(() => {
          listener?.({ data: { id: msg.id, patch: { sources: [], layers: [] }, error: "boom" } });
        });
      }
      addEventListener(_type: string, cb: typeof listener) {
        listener = cb;
      }
      removeEventListener() {}
    }
    vi.stubGlobal("Worker", ErrorWorker as unknown as typeof Worker);

    const patch = await diffSpecsAsync(null, spec);
    expect(patch).toEqual({ sources: [], layers: [] });
  });
});
