import { describe, it, expect, vi, afterEach } from "vitest";
import {
  diffSpecsAsync,
  disposeWorker,
  _resetWorkerBridgeForTests,
  DIFF_WORKER_TIMEOUT_MS,
  DIFF_WORKER_IDLE_MS,
  consumeDiffLastFailed,
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

  // ── FIX-3-6: failed-diff flag (EMPTY_PATCH ≠ genuine no-op) ───────────────

  it("flags a worker-error empty patch as a failed diff, and clears it on success", async () => {
    expect(consumeDiffLastFailed()).toBe(false); // clean slate

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
    // The runtime consumes this flag to avoid advancing appliedSpec.
    expect(consumeDiffLastFailed()).toBe(true);
    expect(consumeDiffLastFailed()).toBe(false); // read-and-clear semantics

    // A subsequent SUCCESSFUL diff (sync fallback) resets the flag too. Drop
    // the crashed worker first — createWorker caches the shared instance.
    _resetWorkerBridgeForTests();
    vi.stubGlobal("Worker", undefined);
    const ok = await diffSpecsAsync(null, spec);
    expect(ok.layers[0].kind).toBe("add");
    expect(consumeDiffLastFailed()).toBe(false);
  });

  it("flags the timeout empty patch as a failed diff too", async () => {
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
    await pending;
    expect(consumeDiffLastFailed()).toBe(true);
  });

  it("does NOT flag a genuine no-op diff (identical specs) as a failure", async () => {
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
      addEventListener(_type: string, cb: typeof listener) { listener = cb; }
      removeEventListener() {}
      terminate() {}
    }
    vi.stubGlobal("Worker", FakeWorker as unknown as typeof Worker);

    await diffSpecsAsync(null, spec);
    const patch = await diffSpecsAsync(spec, spec); // identical → real empty patch
    expect(patch).toEqual({ sources: [], layers: [] });
    expect(consumeDiffLastFailed()).toBe(false); // a genuine no-op, not a failure
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

// ── FE-12: strip inline raster images (base64 heatmaps) ────────────────────
//
// Raster heatmap sources carry the full base64 image as `imageRef`
// (data:image/png;base64,<multi-MB body>). Unlike geojson inlineData (FE-02)
// these used to pass through stripInlineForTransfer, so postMessage
// structured-cloned the whole image on EVERY reconcile — as `prev` AND `next`.
// FE-12 tokenizes `imageRef` by content so the payload boundary carries only
// a stable identity token and unchanged images diff to a no-op.

const MEGABYTE = 1024 * 1024;
const HEATMAP_BODY = "a".repeat(2 * MEGABYTE);
const HEATMAP_IMAGE = `data:image/png;base64,${HEATMAP_BODY}`;

function rasterSpec(imageRef: string, bounds: [number, number, number, number] = [116.2, 39.8, 116.6, 40.1]): MapSpec {
  return {
    version: "1.0",
    sources: {
      H: { type: "raster", imageRef, bounds } as any,
    },
    layers: [
      { id: "H__raster", source: "H", type: "raster", paint: { "raster-opacity": 0.85 } as any },
    ],
  };
}

/** Fake worker that records every postMessage payload and replies with the sync diff. */
class RecordingWorker {
  static instances: RecordingWorker[] = [];
  captured: Array<{ id: string; prev: MapSpec | null; next: MapSpec }> = [];
  private listeners: Array<(event: { data: { id: string; patch: ReturnType<typeof diffSpecs> } }) => void> = [];
  constructor(_url: URL, _opts?: { type?: string }) {
    RecordingWorker.instances.push(this);
  }
  postMessage(msg: { id: string; prev: MapSpec | null; next: MapSpec }) {
    this.captured.push(msg);
    const { id, prev, next } = msg;
    queueMicrotask(() => {
      const patch = diffSpecs(prev, next);
      for (const cb of this.listeners) cb({ data: { id, patch } });
    });
  }
  addEventListener(_type: string, cb: (event: { data: { id: string; patch: ReturnType<typeof diffSpecs> } }) => void) {
    this.listeners.push(cb);
  }
  removeEventListener() {}
  terminate() {}
}

/** The single RecordingWorker instance created by the current test. */
function latestRecorder(): RecordingWorker {
  return RecordingWorker.instances[RecordingWorker.instances.length - 1];
}

describe("diffSpecsAsync raster images (FE-12)", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    _resetWorkerBridgeForTests();
  });

  it("does NOT send the base64 image bytes across postMessage (FE-12)", async () => {
    vi.stubGlobal("Worker", RecordingWorker as unknown as typeof Worker);

    const patch = await diffSpecsAsync(null, rasterSpec(HEATMAP_IMAGE));

    // The posted `next` source must carry an identity token, not the image.
    expect((latestRecorder().captured[0].next.sources.H as any).imageRef).toEqual({ __inlineToken: expect.any(Number) });
    // The multi-MB base64 body must not appear anywhere in the payload.
    const serialized = JSON.stringify(latestRecorder().captured[0]);
    expect(serialized).not.toContain(HEATMAP_BODY);
    // Rehydration: the resolved patch carries the REAL imageRef back so
    // MapSpecRuntime.applySource can add the image source.
    const addChange = patch.sources.find((c) => c.id === "H");
    expect(addChange?.kind).toBe("add");
    expect((addChange?.next as any)?.imageRef).toBe(HEATMAP_IMAGE);
  });

  it("an unchanged image crosses as a tiny token-only payload on every reconcile (FE-12)", async () => {
    vi.stubGlobal("Worker", RecordingWorker as unknown as typeof Worker);

    // Three reconciles with the same image content — fresh spec objects each
    // time, exactly like hudStateToMapSpec rebuilds the spec per reconcile.
    const spec1 = rasterSpec(HEATMAP_IMAGE);
    await diffSpecsAsync(null, spec1);
    const spec2 = rasterSpec(HEATMAP_IMAGE);
    const patch2 = await diffSpecsAsync(spec1, spec2);
    const spec3 = rasterSpec(HEATMAP_IMAGE);
    const patch3 = await diffSpecsAsync(spec2, spec3);

    // Unchanged image → no source change on either subsequent reconcile.
    expect(patch2.sources).toEqual([]);
    expect(patch3.sources).toEqual([]);

    // None of the three payloads ever carries the image bytes, and each stays
    // tiny (a token + bounds + the one layer), never multi-MB.
    const captured = latestRecorder().captured;
    expect(captured).toHaveLength(3);
    for (let i = 0; i < captured.length; i++) {
      const serialized = JSON.stringify(captured[i]);
      expect(serialized, `reconcile ${i + 1} inline-shipped the image in ${serialized.length} bytes`).not.toContain(HEATMAP_BODY);
      expect(serialized.length, `reconcile ${i + 1} shipped ${serialized.length} bytes`).toBeLessThan(16 * 1024);
    }
  });

  it("a changed image invalidates the token and reports a source update (FE-12)", async () => {
    vi.stubGlobal("Worker", RecordingWorker as unknown as typeof Worker);

    const spec1 = rasterSpec(HEATMAP_IMAGE);
    await diffSpecsAsync(null, spec1);
    const newBody = "b".repeat(2 * MEGABYTE);
    const spec2 = rasterSpec(`data:image/png;base64,${newBody}`);
    const patch = await diffSpecsAsync(spec1, spec2);

    expect(patch.sources).toHaveLength(1);
    expect(patch.sources[0].kind).toBe("update");
    // Rehydrated next carries the NEW image — the runtime applies fresh bytes.
    expect((patch.sources[0].next as any)?.imageRef).toBe((spec2.sources.H as any).imageRef);
    // The new bytes never cross postMessage either.
    expect(JSON.stringify(latestRecorder().captured[1])).not.toContain(newBody);
  });

  it("bounds changes still produce an update when only the image bytes are tokenized (FE-12)", async () => {
    vi.stubGlobal("Worker", RecordingWorker as unknown as typeof Worker);

    const spec1 = rasterSpec(HEATMAP_IMAGE, [116.2, 39.8, 116.6, 40.1]);
    await diffSpecsAsync(null, spec1);
    const spec2 = rasterSpec(HEATMAP_IMAGE, [116.2, 39.7, 116.6, 40.2]);
    const patch = await diffSpecsAsync(spec1, spec2);

    expect(patch.sources).toHaveLength(1);
    expect(patch.sources[0].kind).toBe("update");
    expect((patch.sources[0].next as any)?.imageRef).toBe(HEATMAP_IMAGE);
    expect((patch.sources[0].next as any)?.bounds).toEqual([116.2, 39.7, 116.6, 40.2]);
  });
});
