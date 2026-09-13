import { describe, expect, it, beforeEach } from "vitest";
import {
  DEFAULT_SYMBOL_LAW,
  circleRadiusExpression,
  densitySignal,
  density_signal,
  getSymbolLawEvidence,
  heatmapRadiusExpression,
  lineWidthExpression,
  opacityForCount,
  recordSymbolLawEvidence,
  resetSymbolLawEvidence,
  resolveDensityPresentation,
  strokeWidthExpression,
  DEFAULT_DENSITY_THRESHOLDS,
} from "./symbol-law";

/** 按 MapLibre interpolate 语义在 JS 侧求值 zoom 插值表达式（线性）。 */
function evalZoomInterpolate(expr: unknown[], zoom: number): number {
  expect(expr[0]).toBe("interpolate");
  expect(expr[1]).toEqual(["linear"]);
  expect(expr[2]).toEqual(["zoom"]);
  const stops: Array<[number, number]> = [];
  for (let i = 3; i + 1 < expr.length; i += 2) {
    stops.push([expr[i] as number, expr[i + 1] as number]);
  }
  if (zoom <= stops[0][0]) return stops[0][1];
  for (let i = 0; i < stops.length - 1; i++) {
    const [z0, v0] = stops[i];
    const [z1, v1] = stops[i + 1];
    if (zoom <= z1) return v0 + ((v1 - v0) * (zoom - z0)) / (z1 - z0);
  }
  return stops[stops.length - 1][1];
}

describe("symbol-law: circleRadiusExpression (P1)", () => {
  it("produces a zoom interpolate expression with default table", () => {
    const expr = circleRadiusExpression({ featureCount: 100 });
    expect(expr[0]).toBe("interpolate");
    expect(expr[1]).toEqual(["linear"]);
    expect(expr[2]).toEqual(["zoom"]);
    // 停靠点数量与默认表一致。
    expect((expr.length - 3) / 2).toBe(DEFAULT_SYMBOL_LAW.circle.radiusZoomScale.length);
  });

  it("radius grows smoothly with zoom (fixes 放大后点还是那么大)", () => {
    const expr = circleRadiusExpression({ featureCount: 100 });
    const r4 = evalZoomInterpolate(expr, 4);
    const r8 = evalZoomInterpolate(expr, 8);
    const r12 = evalZoomInterpolate(expr, 12);
    const r16 = evalZoomInterpolate(expr, 16);
    expect(r8).toBeGreaterThan(r4);
    expect(r12).toBeGreaterThan(r8);
    expect(r16).toBeGreaterThan(r12);
    // 锚点：zoom=8 处应回到锚点半径 6（scale=1.0）。
    expect(r8).toBeCloseTo(6, 5);
  });

  it("radius shrinks as feature count grows (fixes 1 万个点挤成一团)", () => {
    const sparse = evalZoomInterpolate(circleRadiusExpression({ featureCount: 100 }), 8);
    const dense = evalZoomInterpolate(circleRadiusExpression({ featureCount: 6000 }), 8);
    const veryDense = evalZoomInterpolate(circleRadiusExpression({ featureCount: 30000 }), 8);
    expect(dense).toBeLessThan(sparse);
    expect(veryDense).toBeLessThan(dense);
  });

  it("respects min/max clamps and is deterministic", () => {
    const tiny = circleRadiusExpression({ featureCount: 1e9, overrides: { radiusMin: 3 } });
    const stops = tiny.slice(3).filter((_, i) => i % 2 === 0) as number[];
    // 极密时缩到 0.55×6=3.3，clamp min=3 生效下界之上。
    for (const v of (tiny.slice(4).filter((_, i) => i % 2 === 0) as number[])) {
      expect(v).toBeGreaterThanOrEqual(3);
    }
    expect(stops.length).toBeGreaterThan(0);
    expect(circleRadiusExpression({ featureCount: 500 })).toEqual(
      circleRadiusExpression({ featureCount: 500 }),
    );
  });

  it("unknown count (NaN/0/negative) falls back to anchor defaults", () => {
    const unknown = circleRadiusExpression({});
    const nan = circleRadiusExpression({ featureCount: Number.NaN });
    const neg = circleRadiusExpression({ featureCount: -5 });
    expect(nan).toEqual(unknown);
    expect(neg).toEqual(unknown);
  });
});

describe("symbol-law: lineWidthExpression (P1)", () => {
  it("line width increases gently with zoom and decreases with density", () => {
    const expr = lineWidthExpression({ featureCount: 100 });
    const w4 = evalZoomInterpolate(expr, 4);
    const w10 = evalZoomInterpolate(expr, 10);
    const w16 = evalZoomInterpolate(expr, 16);
    expect(w10).toBeGreaterThan(w4);
    expect(w16).toBeGreaterThan(w10);
    const dense = evalZoomInterpolate(lineWidthExpression({ featureCount: 30000 }), 10);
    expect(dense).toBeLessThan(w10);
  });

  it("clamps to the width floor", () => {
    const expr = lineWidthExpression({ featureCount: 1e9, overrides: { widthMin: 0.5 } });
    for (const v of expr.slice(4).filter((_, i) => i % 2 === 0) as unknown as number[]) {
      expect(v).toBeGreaterThanOrEqual(0.5);
    }
  });
});

describe("symbol-law: heatmapRadiusExpression (P1)", () => {
  it("contract base radius anchors the zoom ramp; radius expands with zoom", () => {
    const expr = heatmapRadiusExpression({ baseRadiusPx: 30, featureCount: 100 });
    const r3 = evalZoomInterpolate(expr, 3);
    const r8 = evalZoomInterpolate(expr, 8);
    const r16 = evalZoomInterpolate(expr, 16);
    expect(r8).toBeCloseTo(30, 0);
    expect(r8).toBeGreaterThan(r3);
    expect(r16).toBeGreaterThan(r8);
    expect(r16).toBeLessThanOrEqual(DEFAULT_SYMBOL_LAW.heatmap.radiusMax);
  });

  it("shrinks under extreme density but never below the contract floor", () => {
    const expr = heatmapRadiusExpression({ baseRadiusPx: 30, featureCount: 60000 });
    for (const v of expr.slice(4).filter((_, i) => i % 2 === 0) as unknown as number[]) {
      expect(v).toBeGreaterThanOrEqual(DEFAULT_SYMBOL_LAW.heatmap.radiusMin);
    }
    const normal = evalZoomInterpolate(heatmapRadiusExpression({ baseRadiusPx: 30, featureCount: 1000 }), 8);
    const dense = evalZoomInterpolate(heatmapRadiusExpression({ baseRadiusPx: 30, featureCount: 60000 }), 8);
    expect(dense).toBeLessThan(normal);
  });

  it("keeps the resolveHeatmapRadiusPx clamp window semantics", () => {
    // 显式契约值 4（下限）与 80（上限）都合法落进表达式锚点。
    expect(evalZoomInterpolate(heatmapRadiusExpression({ baseRadiusPx: 4 }), 8)).toBe(4);
    expect(evalZoomInterpolate(heatmapRadiusExpression({ baseRadiusPx: 80 }), 8)).toBe(80);
    expect(evalZoomInterpolate(heatmapRadiusExpression({ baseRadiusPx: 9999 }), 8)).toBe(80);
  });
});

describe("symbol-law: opacityForCount (P1)", () => {
  it("anchors at 0.8 and downshifts with density", () => {
    expect(opacityForCount({ featureCount: 100 })).toBeCloseTo(0.8, 5);
    expect(opacityForCount({ featureCount: 6000 })).toBeCloseTo(0.65, 5);
    expect(opacityForCount({ featureCount: 25000 })).toBeCloseTo(0.5, 5);
    expect(opacityForCount({})).toBeCloseTo(0.8, 5);
  });

  it("is monotone non-increasing in count", () => {
    let prev = 1;
    for (const c of [0, 100, 1000, 4999, 5000, 5001, 19999, 20000, 100000]) {
      const v = opacityForCount({ featureCount: c });
      expect(v).toBeLessThanOrEqual(prev);
      prev = v;
    }
  });
});

describe("symbol-law: strokeWidthExpression", () => {
  it("scales the anchor over zoom", () => {
    const expr = strokeWidthExpression(2);
    expect(evalZoomInterpolate(expr, 4)).toBeCloseTo(2 * 0.7, 3);
    expect(evalZoomInterpolate(expr, 10)).toBeCloseTo(2, 3);
  });
});

describe("symbol-law: densitySignal (P1, 05 线共享)", () => {
  it("levels and score are monotone in count", () => {
    expect(densitySignal(0).level).toBe("sparse");
    expect(densitySignal(500).level).toBe("sparse");
    expect(densitySignal(2000).level).toBe("normal");
    expect(densitySignal(6000).level).toBe("dense");
    expect(densitySignal(30000).level).toBe("very-dense");
    expect(densitySignal(100).score).toBeLessThan(densitySignal(1000).score);
    expect(densitySignal(100000).score).toBe(1);
  });

  it("non-finite counts degrade to sparse", () => {
    expect(densitySignal(Number.NaN).level).toBe("sparse");
    expect(densitySignal(-1).score).toBe(0);
  });

  it("exports the snake_case contract alias for the 05 line", () => {
    expect(density_signal).toBe(densitySignal);
  });

  it("uses the VIEWPORT_RENDER_BUDGET=5000 baseline for the dense cut", () => {
    expect(DEFAULT_DENSITY_THRESHOLDS.cluster).toBe(5000);
    expect(densitySignal(4999).level).toBe("normal");
    expect(densitySignal(5000).level).toBe("dense");
  });
});

describe("symbol-law: resolveDensityPresentation (P2)", () => {
  it("points switch to cluster at the 5000 baseline and heatmap beyond", () => {
    const c = resolveDensityPresentation({ geometryType: "Point", featureCount: 6000 });
    expect(c.mode).toBe("cluster");
    expect(c.reason).toContain("6000");
    const h = resolveDensityPresentation({ geometryType: "Point", featureCount: 25000 });
    expect(h.mode).toBe("heatmap");
  });

  it("sparse points and polygons stay native", () => {
    expect(resolveDensityPresentation({ geometryType: "Point", featureCount: 800 }).mode).toBe("native");
    expect(resolveDensityPresentation({ geometryType: "Polygon", featureCount: 50000 }).mode).toBe("native");
  });

  it("explicit cluster config wins (显式意图优先)", () => {
    const r = resolveDensityPresentation({
      geometryType: "Point",
      featureCount: 30000,
      explicitCluster: true,
    });
    expect(r.mode).toBe("native");
  });

  it("thresholds are configurable", () => {
    const r = resolveDensityPresentation({
      geometryType: "Point",
      featureCount: 900,
      thresholds: { cluster: 800 },
    });
    expect(r.mode).toBe("cluster");
  });

  it("dense lines keep native mode but carry a downgrade reason", () => {
    const r = resolveDensityPresentation({ geometryType: "LineString", featureCount: 6000 });
    expect(r.mode).toBe("native");
    expect(r.reason).toContain("width downscale");
  });
});

describe("symbol-law: evidence ring", () => {
  beforeEach(() => resetSymbolLawEvidence());

  it("records bounded events with counters", () => {
    for (let i = 0; i < 70; i++) {
      recordSymbolLawEvidence("law-applied", { i }, `layer-${i}`);
    }
    const snap = getSymbolLawEvidence();
    expect(snap.events.length).toBe(64);
    expect(snap.counts["law-applied"]).toBe(70);
    expect(snap.events[0].detail.i).toBe(6); // 最旧被覆盖
    expect(snap.events[63].detail.i).toBe(69);
    expect(snap.events[63].id).toBe("layer-69");
  });

  it("counts per kind", () => {
    recordSymbolLawEvidence("density-switch", { from: "native", to: "cluster" });
    recordSymbolLawEvidence("density-switch", { from: "cluster", to: "heatmap" });
    recordSymbolLawEvidence("unmapped-paint-key", { key: "fillOutlineWidth" });
    const snap = getSymbolLawEvidence();
    expect(snap.counts["density-switch"]).toBe(2);
    expect(snap.counts["unmapped-paint-key"]).toBe(1);
  });
});
