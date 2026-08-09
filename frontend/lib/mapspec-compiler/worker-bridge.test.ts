import { describe, it, expect, vi, afterEach } from "vitest";
import {
  diffSpecsAsync,
  disposeWorker,
  _resetWorkerBridgeForTests,
  DIFF_WORKER_TIMEOUT_MS,
  DIFF_WORKER_IDLE_MS,
} from "./worker-bridge";
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
    vi.useRealTimers();
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
      terminate() {}
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
      terminate() {}
    }
    vi.stubGlobal("Worker", ErrorWorker as unknown as typeof Worker);

    const patch = await diffSpecsAsync(null, spec);
    expect(patch).toEqual({ sources: [], layers: [] });
  });

  it("resolves an empty patch when the worker errors before posting", async () => {
    class FailingWorker {
      onerror: ((event: ErrorEvent) => void) | null = null;
      constructor(_url: URL, _opts?: { type?: string }) {}
      postMessage() {
        // Fail like a crashed worker: fire `onerror` and never post a response.
        queueMicrotask(() => this.onerror?.({ type: "error" } as ErrorEvent));
      }
      addEventListener() {}
      removeEventListener() {}
      terminate() {}
    }
    vi.stubGlobal("Worker", FailingWorker as unknown as typeof Worker);

    const patch = await diffSpecsAsync(null, spec);
    expect(patch).toEqual({ sources: [], layers: [] });
  });

  it("resolves an empty patch when the worker exceeds the timeout", async () => {
    class SilentWorker {
      constructor(_url: URL, _opts?: { type?: string }) {}
      postMessage() {} // Never responds — simulates a wedged worker.
      addEventListener() {}
      removeEventListener() {}
      terminate() {}
    }
    vi.stubGlobal("Worker", SilentWorker as unknown as typeof Worker);

    vi.useFakeTimers();
    const pending = diffSpecsAsync(null, spec);
    await vi.advanceTimersByTimeAsync(DIFF_WORKER_TIMEOUT_MS);
    const patch = await pending;
    expect(patch).toEqual({ sources: [], layers: [] });
  });

  it("only terminates worker when no requests are pending", async () => {
    let terminateCalls = 0;
    const listeners: Array<(event: { data: { id: string; patch: ReturnType<typeof diffSpecs> } }) => void> = [];
    class MultiWorker {
      constructor(_url: URL, _opts?: { type?: string }) {}
      postMessage(msg: { id: string; prev: MapSpec | null; next: MapSpec }) {
        const { id, prev, next } = msg;
        queueMicrotask(() => {
          const patch = diffSpecs(prev, next);
          for (const cb of listeners) cb({ data: { id, patch } });
        });
      }
      addEventListener(_type: string, cb: any) {
        listeners.push(cb);
      }
      removeEventListener(_type: string, cb: any) {
        const idx = listeners.indexOf(cb);
        if (idx >= 0) listeners.splice(idx, 1);
      }
      terminate() {
        terminateCalls++;
      }
    }
    vi.stubGlobal("Worker", MultiWorker as unknown as typeof Worker);

    // FE-01: with two CONCURRENT requests, the worker must stay alive while at
    // least one is still pending, then terminate only after the idle window.
    vi.useFakeTimers();
    const p1 = diffSpecsAsync(null, spec);
    const p2 = diffSpecsAsync(null, spec);
    await Promise.all([p1, p2]);
    // Immediately after both settle: worker kept warm (idle timer armed, not fired).
    expect(terminateCalls).toBe(0);
    // After the idle window elapses with no new work, the worker is torn down.
    await vi.advanceTimersByTimeAsync(DIFF_WORKER_IDLE_MS);
    expect(terminateCalls).toBe(1);
  });

  // ── FE-01: keep the worker warm instead of terminating at 0 ──────────────

  it("keeps the worker warm after a single reconcile completes (FE-01)", async () => {
    let terminateCalls = 0;
    let listener: ((event: { data: { id: string; patch: ReturnType<typeof diffSpecs> } }) => void) | null = null;
    class WarmWorker {
      constructor(_url: URL, _opts?: { type?: string }) {}
      postMessage(msg: { id: string; prev: MapSpec | null; next: MapSpec }) {
        const { id, prev, next } = msg;
        queueMicrotask(() => {
          const patch = diffSpecs(prev, next);
          listener?.({ data: { id, patch } });
        });
      }
      addEventListener(_type: string, cb: typeof listener) { listener = cb; }
      removeEventListener() {}
      terminate() { terminateCalls++; }
    }
    vi.stubGlobal("Worker", WarmWorker as unknown as typeof Worker);
    vi.useFakeTimers();

    await diffSpecsAsync(null, spec);
    // Reconcile fully settled, count back to 0 — worker must NOT be terminated
    // immediately (stays warm for the idle window).
    expect(terminateCalls).toBe(0);

    // And not until the idle window elapses with no new work.
    await vi.advanceTimersByTimeAsync(DIFF_WORKER_IDLE_MS - 1);
    expect(terminateCalls).toBe(0);
    await vi.advanceTimersByTimeAsync(1);
    expect(terminateCalls).toBe(1);
  });

  it("disposeWorker() tears down the warm worker immediately (FE-01)", async () => {
    let terminateCalls = 0;
    let listener: ((event: { data: { id: string; patch: ReturnType<typeof diffSpecs> } }) => void) | null = null;
    class WarmWorker {
      constructor(_url: URL, _opts?: { type?: string }) {}
      postMessage(msg: { id: string; prev: MapSpec | null; next: MapSpec }) {
        const { id, prev, next } = msg;
        queueMicrotask(() => {
          const patch = diffSpecs(prev, next);
          listener?.({ data: { id, patch } });
        });
      }
      addEventListener(_type: string, cb: typeof listener) { listener = cb; }
      removeEventListener() {}
      terminate() { terminateCalls++; }
    }
    vi.stubGlobal("Worker", WarmWorker as unknown as typeof Worker);

    await diffSpecsAsync(null, spec);
    expect(terminateCalls).toBe(0); // warm, idle timer armed

    disposeWorker();
    expect(terminateCalls).toBe(1); // torn down now, not after idle window
  });

  it("a second reconcile reuses the warm worker (no re-boot) within the idle window (FE-01)", async () => {
    let instances = 0;
    let listener: ((event: { data: { id: string; patch: ReturnType<typeof diffSpecs> } }) => void) | null = null;
    class WarmWorker {
      constructor(_url: URL, _opts?: { type?: string }) { instances++; }
      postMessage(msg: { id: string; prev: MapSpec | null; next: MapSpec }) {
        const { id, prev, next } = msg;
        queueMicrotask(() => {
          const patch = diffSpecs(prev, next);
          listener?.({ data: { id, patch } });
        });
      }
      addEventListener(_type: string, cb: typeof listener) { listener = cb; }
      removeEventListener() {}
      terminate() {}
    }
    vi.stubGlobal("Worker", WarmWorker as unknown as typeof Worker);

    await diffSpecsAsync(null, spec);
    await diffSpecsAsync(null, spec); // back-to-back reconcile (serialized)
    expect(instances).toBe(1); // same worker reused, not re-created
  });

  // ── FE-02: strip inline GeoJSON from the postMessage payload ─────────────

  it("does NOT send inline GeoJSON coordinates across postMessage (FE-02)", async () => {
    const bigCoords = [[0, 0], [1, 1], [2, 2], [3, 3]];
    const bigInlineData = {
      type: "FeatureCollection",
      features: [
        { type: "Feature", properties: {}, geometry: { type: "LineString", coordinates: bigCoords } },
      ],
    };
    const specWithInline: MapSpec = {
      version: "1.0",
      sources: { S: { type: "geojson", inlineData: bigInlineData } as any },
      layers: [{ id: "S__line", source: "S", type: "line", paint: { "line-color": "#000" } }],
    };

    let capturedPayload: any = null;
    let listener: ((event: { data: { id: string; patch: ReturnType<typeof diffSpecs> } }) => void) | null = null;
    class CapturingWorker {
      constructor(_url: URL, _opts?: { type?: string }) {}
      postMessage(msg: { id: string; prev: MapSpec | null; next: MapSpec }) {
        capturedPayload = msg;
        const { id, prev, next } = msg;
        queueMicrotask(() => {
          const patch = diffSpecs(prev, next);
          listener?.({ data: { id, patch } });
        });
      }
      addEventListener(_type: string, cb: typeof listener) { listener = cb; }
      removeEventListener() {}
      terminate() {}
    }
    vi.stubGlobal("Worker", CapturingWorker as unknown as typeof Worker);

    const patch = await diffSpecsAsync(null, specWithInline);
    // The posted `next` source must carry an identity token, NOT the coordinates.
    const sentNextSource = capturedPayload.next.sources.S;
    expect(sentNextSource.inlineData).toEqual({ __inlineToken: expect.any(Number) });
    // Sentinel: the actual coordinates must NOT appear anywhere in the payload.
    const serialized = JSON.stringify(capturedPayload);
    expect(serialized).not.toContain(JSON.stringify(bigCoords));
    expect(serialized).not.toContain("FeatureCollection");

    // Rehydration: the resolved patch must carry the REAL inlineData back so
    // MapSpecRuntime.applySource can add the GeoJSON source.
    const addChange = patch.sources.find((c) => c.id === "S");
    expect(addChange?.kind).toBe("add");
    expect((addChange?.next as any)?.inlineData).toBe(bigInlineData);
  });

  it("treats identical inline data (same reference) as unchanged across reconciles (FE-02)", async () => {
    // Two reconciles whose source reuses the SAME inline data object (the
    // adapter reuses layer.source by reference) must diff to a no-op patch,
    // exactly as before stripping.
    const inlineData = { type: "FeatureCollection", features: [] };
    const specA: MapSpec = {
      version: "1.0",
      sources: { S: { type: "geojson", inlineData } as any },
      layers: [{ id: "S__point", source: "S", type: "circle", paint: { "circle-radius": 6 } }],
    };
    const specB: MapSpec = {
      version: "1.0",
      sources: { S: { type: "geojson", inlineData } as any },
      layers: [{ id: "S__point", source: "S", type: "circle", paint: { "circle-radius": 6 } }],
    };

    let listener: ((event: { data: { id: string; patch: ReturnType<typeof diffSpecs> } }) => void) | null = null;
    class WarmWorker {
      constructor(_url: URL, _opts?: { type?: string }) {}
      postMessage(msg: { id: string; prev: MapSpec | null; next: MapSpec }) {
        const { id, prev, next } = msg;
        queueMicrotask(() => {
          const patch = diffSpecs(prev, next);
          listener?.({ data: { id, patch } });
        });
      }
      addEventListener(_type: string, cb: typeof listener) { listener = cb; }
      removeEventListener() {}
      terminate() {}
    }
    vi.stubGlobal("Worker", WarmWorker as unknown as typeof Worker);

    await diffSpecsAsync(null, specA); // first apply
    const patch = await diffSpecsAsync(specA, specB); // second reconcile, same data
    expect(patch.sources).toEqual([]);
    expect(patch.layers).toEqual([]);
  });

  it("treats genuinely changed inline data as a source update (FE-02)", async () => {
    const dataA = { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [0, 0] } }] };
    const dataB = { type: "FeatureCollection", features: [{ type: "Feature", properties: {}, geometry: { type: "Point", coordinates: [1, 1] } }] };
    const specA: MapSpec = {
      version: "1.0",
      sources: { S: { type: "geojson", inlineData: dataA } as any },
      layers: [{ id: "S__point", source: "S", type: "circle", paint: { "circle-radius": 6 } }],
    };
    const specB: MapSpec = {
      version: "1.0",
      sources: { S: { type: "geojson", inlineData: dataB } as any },
      layers: [{ id: "S__point", source: "S", type: "circle", paint: { "circle-radius": 6 } }],
    };

    let listener: ((event: { data: { id: string; patch: ReturnType<typeof diffSpecs> } }) => void) | null = null;
    class WarmWorker {
      constructor(_url: URL, _opts?: { type?: string }) {}
      postMessage(msg: { id: string; prev: MapSpec | null; next: MapSpec }) {
        const { id, prev, next } = msg;
        queueMicrotask(() => {
          const patch = diffSpecs(prev, next);
          listener?.({ data: { id, patch } });
        });
      }
      addEventListener(_type: string, cb: typeof listener) { listener = cb; }
      removeEventListener() {}
      terminate() {}
    }
    vi.stubGlobal("Worker", WarmWorker as unknown as typeof Worker);

    await diffSpecsAsync(null, specA);
    const patch = await diffSpecsAsync(specA, specB);
    expect(patch.sources).toHaveLength(1);
    expect(patch.sources[0].kind).toBe("update");
    // Rehydrated next carries the new inline data.
    expect((patch.sources[0].next as any)?.inlineData).toBe(dataB);
  });
});
