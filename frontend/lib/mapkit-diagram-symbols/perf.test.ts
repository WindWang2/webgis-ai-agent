/**
 * perf.test.ts —— 前端帧时基线（WP5b）：oracle→布局→三角化全链在
 * 10/100/1k 图符下的 CPU 耗时（render() 的 GPU 部分在浏览器实测入账）。
 *
 * 断言宽松兜底（防病态复杂度），实测数字打印进 vitest 输出并入账。
 */
import { describe, expect, it } from "vitest";
import { buildGlyphGeometry, computeGlyphScale, glyphBoundRadius,
         normalizeSizePolicy } from "./oracle";
import { solveDiagramSymbolLayout } from "./layout";
import { triangulateGlyph } from "./vertices";

function snakeFeatures(n: number) {
  const side = Math.ceil(Math.sqrt(n));
  const feats = [];
  for (let i = 0; i < n; i++) {
    feats.push({
      type: "Feature", id: `f${String(i).padStart(6, "0")}`,
      geometry: { type: "Point", coordinates: [104 + (i % side) * 0.01, 30 + Math.floor(i / side) * 0.01] },
      properties: { v0: (i * 7) % 100 + 1, v1: (i * 13) % 50 + 1 },
    });
  }
  return feats;
}

function fullChain(n: number): { buildMs: number; triangulateMs: number; count: number } {
  const feats = snakeFeatures(n);
  const sp = normalizeSizePolicy("composition_pie",
    { r_max_px: 24, total_area_encoding: true, r_min_px: 2 });
  const t0 = performance.now();
  const values = feats.map((f) => [["c0", f.properties.v0], ["c1", f.properties.v1]] as const);
  const scale = computeGlyphScale("composition_pie", values as never, sp);
  const ids: string[] = [];
  const radii: number[] = [];
  const geomById = new Map<string, ReturnType<typeof buildGlyphGeometry>>();
  values.forEach((row, i) => {
    const geo = buildGlyphGeometry("composition_pie", row as never, scale, sp);
    ids.push(feats[i].id);
    geomById.set(feats[i].id, geo);
    radii.push(glyphBoundRadius(geo));
  });
  const plan = solveDiagramSymbolLayout(feats as never,
    { values: values as never, glyph_feature_ids: ids }, radii, {});
  const t1 = performance.now();
  let tri = 0;
  for (const g of plan.glyphs) {
    const geo = geomById.get(g.feature_id);
    if (geo) { triangulateGlyph(geo, ["#e41a1c", "#377eb8"]); tri += 1; }
  }
  void tri;
  const t2 = performance.now();
  return { buildMs: t1 - t0, triangulateMs: t2 - t1, count: plan.glyphs.length };
}

describe("diagram symbols frontend chain baseline", () => {
  it("10/100/1k build+triangulate within loose bounds", () => {
    for (const n of [10, 100, 1000]) {
      const r = fullChain(n);
      console.log(`[diagram-frame-perf] n=${n} build+layout=${r.buildMs.toFixed(1)}ms `
        + `triangulate(${r.count})=${r.triangulateMs.toFixed(1)}ms`);
      expect(r.count).toBeGreaterThan(0);
      if (n === 1000) expect(r.buildMs + r.triangulateMs).toBeLessThan(30_000);
    }
  });
});
