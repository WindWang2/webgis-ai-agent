import { describe, it } from "vitest";
import { compileMapSpec } from "./compiler";
import { diffSpecs, type SpecPatch } from "./reconciler";
import type { MapSpec } from "./types";

/**
 * AC-06 (ADR-0155) P0/P7 — 10k-feature diff/compile baseline harness.
 *
 * Deterministic (seeded LCG geometry, no timers in the measured paths).
 * Scenarios mirror the reconciliation cost model that matters in production:
 *
 *   compile_10k        — headless compile of a 10k-point + 2k-polygon spec.
 *   diff_paint_only    — layer paint color change, source objects shared by
 *                        identity (memoized compose → today: layer-key walk).
 *   diff_one_changed   — ONE feature property edited out of 10k (stream
 *                        update) → today: full coordinate walk of the
 *                        FeatureCollection on every diff.
 *   diff_rev_equal     — same data version (content_revision equal) but the
 *                        source object was rebuilt (new identity) → today:
 *                        full walk; the revision short-circuit makes it O(1).
 *
 * The test logs `PERF|<scenario>|<median_ms>` lines (gate evidence is captured
 * from terminal output — P0 baseline on origin/master code, P7 after the
 * content_revision/hash short-circuits). Assertions only pin the patch SHAPE
 * (which kind each scenario must produce) so the harness never flakes on
 * machine speed; the timing columns are observational.
 */

function lcg(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s * 1664525 + 1013904223) >>> 0;
    return s / 0x100000000;
  };
}

function buildFeatureCollection(points: number, polygons: number) {
  const rand = lcg(20260913);
  const features: any[] = [];
  for (let i = 0; i < points; i++) {
    features.push({
      type: "Feature",
      id: i,
      properties: { value: rand() * 100, category: i % 5, name: `p${i}` },
      geometry: { type: "Point", coordinates: [100 + rand() * 10, 30 + rand() * 10] },
    });
  }
  for (let i = 0; i < polygons; i++) {
    const x = 100 + rand() * 9;
    const y = 30 + rand() * 9;
    features.push({
      type: "Feature",
      id: 1_000_000 + i,
      properties: { value: rand() * 100, category: i % 5, name: `g${i}` },
      geometry: {
        type: "Polygon",
        coordinates: [[[x, y], [x + 0.5, y], [x + 0.5, y + 0.5], [x, y + 0.5], [x, y]]],
      },
    });
  }
  return { type: "FeatureCollection", features };
}

function buildSpec(inlineData: any): MapSpec {
  return {
    version: "1.0",
    sources: {
      pts: { type: "geojson", inlineData },
    },
    layers: [
      {
        id: "pts-main",
        source: "pts",
        type: "circle",
        paint: { color: "#3182bd", radius: 6, opacity: 0.8 },
      },
      {
        id: "pts-fill",
        source: "pts",
        type: "fill",
        paint: { color: "#f33", opacity: 0.35 },
        filter: ["==", ["get", "category"], 1],
      },
    ],
  };
}

function deepClone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v)) as T;
}

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  return s[Math.floor(s.length / 2)];
}

function timeMedian(runs: number, fn: () => void): number {
  const samples: number[] = [];
  for (let i = 0; i < 2; i++) fn(); // warmup
  for (let i = 0; i < runs; i++) {
    const t0 = performance.now();
    fn();
    samples.push(performance.now() - t0);
  }
  return median(samples);
}

const RUNS = 15;

describe("ac-06 perf harness (10k features) — baseline vs patch", () => {
  const fc = buildFeatureCollection(10_000, 2_000);
  const base = buildSpec(fc);

  it("logs scenario timings", () => {
    // compile_10k
    const compileMs = timeMedian(RUNS, () => {
      compileMapSpec(base);
    });
    console.log(`PERF|compile_10k|${compileMs.toFixed(3)}`);

    // diff_paint_only — same source identity, layer paint changed.
    const paintChanged = buildSpec(fc);
    (paintChanged.layers[0].paint as any).color = "#08519c";
    let paintPatch: SpecPatch | null = null;
    const diffPaintMs = timeMedian(RUNS, () => {
      paintPatch = diffSpecs(base, paintChanged);
    });
    console.log(`PERF|diff_paint_only|${diffPaintMs.toFixed(3)}`);

    // diff_one_changed — one feature property edited (fresh FC identity).
    // Edited feature is the LAST one: deep-compare early-exit would otherwise
    // trivialize the scenario (measures the full-walk worst case).
    const fcChanged = deepClone(fc);
    const lastIdx = fcChanged.features.length - 1;
    (fcChanged.features[lastIdx].properties as any).value = 4242;
    const nextChanged = buildSpec(fcChanged);
    let changedPatch: SpecPatch | null = null;
    const diffOneMs = timeMedian(RUNS, () => {
      changedPatch = diffSpecs(base, nextChanged);
    });
    console.log(`PERF|diff_one_changed|${diffOneMs.toFixed(3)}`);

    // diff_rev_equal — equal content_revision, rebuilt source identity.
    const fcRevA = { ...deepClone(fc) };
    const fcRevB = deepClone(fc);
    const revA = buildSpec(fcRevA);
    const revB = buildSpec(fcRevB);
    (revA.sources.pts as any).content_revision = 7;
    (revB.sources.pts as any).content_revision = 7;
    let revPatch: SpecPatch | null = null;
    const diffRevMs = timeMedian(RUNS, () => {
      revPatch = diffSpecs(revA, revB);
    });
    console.log(`PERF|diff_rev_equal|${diffRevMs.toFixed(3)}`);

    // Shape assertions (correctness of the harness itself — machine-independent).
    const p1 = paintPatch as unknown as SpecPatch;
    const p2 = changedPatch as unknown as SpecPatch;
    const p3 = revPatch as unknown as SpecPatch;
    if (p1.layers.length !== 1 || p1.layers[0].kind !== "paint") {
      throw new Error(`diff_paint_only expected single paint patch, got ${JSON.stringify(p1.layers)}`);
    }
    if (p2.sources.length !== 1 || p2.sources[0].kind !== "update") {
      throw new Error(`diff_one_changed expected source update, got ${JSON.stringify(p2.sources)}`);
    }
    if (p3.sources.length !== 0 || p3.layers.length !== 0) {
      throw new Error(`diff_rev_equal expected empty patch, got ${JSON.stringify({ s: p3.sources, l: p3.layers })}`);
    }
  });
});
