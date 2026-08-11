import { describe, it, expect, vi, afterEach } from "vitest";
import {
  diffSpecsAsync,
  _resetWorkerBridgeForTests,
  _inlineTokenRegistrySizeForTests,
  INLINE_TOKEN_CACHE_MAX,
} from "./worker-bridge";
import { diffSpecs } from "./reconciler";
import type { MapSpec } from "./types";

// FE-3 (design §7): the per-source inline-data registry (lastInlineDataBySource)
// must be FIFO-bounded (findings E4: unbounded Map). The WeakMap token cache
// keeps same-reference identity across eviction, so eviction is harmless.

function distinctFC(seed: number | string): object {
  return {
    type: "FeatureCollection",
    features: [
      { type: "Feature", properties: { seed }, geometry: { type: "Point", coordinates: [seed, 0] } },
    ],
  };
}

function specWith(sourceId: string, data: object): MapSpec {
  return {
    version: "1.0",
    sources: { [sourceId]: { type: "geojson", inlineData: data } },
    layers: [{ id: `${sourceId}__point`, source: sourceId, type: "circle", paint: {} }],
  };
}

/** Worker stub that answers with the same diff the main thread would produce. */
function stubFakeWorker(): void {
  let listener: ((event: { data: { id: string; patch: ReturnType<typeof diffSpecs> } }) => void) | null = null;
  class FakeWorker {
    constructor(_url: URL, _opts?: { type?: string }) {}
    postMessage(msg: { id: string; prev: MapSpec | null; next: MapSpec }) {
      const { id, prev, next } = msg;
      queueMicrotask(() => {
        listener?.({ data: { id, patch: diffSpecs(prev, next) } });
      });
    }
    addEventListener(_type: string, cb: typeof listener) { listener = cb; }
    removeEventListener() {}
    terminate() {}
  }
  vi.stubGlobal("Worker", FakeWorker as unknown as typeof Worker);
}

describe("worker-bridge inline-data registry LRU (FE-3)", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    _resetWorkerBridgeForTests();
  });

  it(`caps lastInlineDataBySource at ${INLINE_TOKEN_CACHE_MAX} entries`, async () => {
    stubFakeWorker();

    // Distinct source id + distinct data object per round-trip — each is a new
    // registry entry; the cap must hold even after overshooting by 5.
    for (let i = 0; i < INLINE_TOKEN_CACHE_MAX + 5; i++) {
      await diffSpecsAsync(null, specWith(`src-${i}`, distinctFC(i)));
    }

    expect(_inlineTokenRegistrySizeForTests()).toBe(INLINE_TOKEN_CACHE_MAX);
  });

  it("keeps same-reference identity across registry eviction (no spurious churn)", async () => {
    stubFakeWorker();
    const data = distinctFC("target");
    await diffSpecsAsync(null, specWith("src-target", data));

    // Evict src-target by filling the registry past the cap with other sources.
    for (let i = 0; i < INLINE_TOKEN_CACHE_MAX; i++) {
      await diffSpecsAsync(null, specWith(`filler-${i}`, distinctFC(`f-${i}`)));
    }
    expect(_inlineTokenRegistrySizeForTests()).toBe(INLINE_TOKEN_CACHE_MAX);

    // Re-diffing the SAME reference still round-trips as a no-op: the WeakMap
    // token cache (unbounded, GC-able) survives registry eviction.
    const patch = await diffSpecsAsync(specWith("src-target", data), specWith("src-target", data));
    expect(patch.sources).toEqual([]);
    expect(patch.layers).toEqual([]);
  });

  it("deep-equal reuse still works while under the cap", async () => {
    stubFakeWorker();
    const data = distinctFC("x");

    // First pass registers the payload for src-a.
    await diffSpecsAsync(null, specWith("src-a", data));

    // A NEW reference that is deep-equal reuses the token → identical specs
    // still diff to "no change" (the FE-02 identity contract).
    const deepEqualCopy = JSON.parse(JSON.stringify(data)) as object;
    const patch = await diffSpecsAsync(specWith("src-a", deepEqualCopy), specWith("src-a", deepEqualCopy));
    expect(patch.sources).toEqual([]);
    expect(patch.layers).toEqual([]);
  });
});
