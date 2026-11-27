/**
 * layout.test.ts —— case 6 ``layout_determinism`` 的位级 parity（POINT 锚点
 * 全值；polygon 用正方形 case 使 scanline 代表点与 shapely
 * representative_point 逐位一致 → digest 全等）+ 锚定/避让/会计单元断言。
 */

import { describe, expect, it } from "vitest";
import fixturesJson from "./fixtures/oracle-fixtures.json";
import {
  computeFeatureAnchor,
  glyphMinZoom,
  lnglatToWorld,
  normalizeLayoutPolicy,
  solveDiagramSymbolLayout,
  worldPxScale,
  worldToLnglat,
  type DiagramFeatureInput,
  type LayoutGlyph,
  type NormalizedLayoutPolicy,
} from "./layout";
import { DiagramSymbolError, type GlyphValue } from "./oracle";

const fixtureCases = (fixturesJson as { cases: unknown[] }).cases;
const layoutCase = fixtureCases[5] as {
  name: string;
  layout_plan: {
    digest: string;
    evidence: Record<string, unknown>;
    glyphs: unknown[];
    schema: string;
  };
  qualification: {
    values: [string, number][][];
    glyph_feature_ids: string[];
  };
};

/**
 * case 6 的要素重建（fixture 只落盘 layout_plan；锚点来源可由 evidence 与
 * glyph 值唯一确定）：big/small 均为 Point [104,30]（small 与 big 同锚，
 * 位移半径不可逃逸 → omitted_collision）；poly 为正方形，其 bbox 中线
 * scanline 中点 = [105.5,30.5]，与 shapely representative_point 位级一致。
 */
const FEATURES: DiagramFeatureInput[] = [
  { type: "Feature", geometry: { type: "Point", coordinates: [104, 30] } },
  { type: "Feature", geometry: { type: "Point", coordinates: [104, 30] } },
  {
    type: "Feature",
    geometry: {
      type: "Polygon",
      coordinates: [[[104.5, 30], [106.5, 30], [106.5, 31], [104.5, 31], [104.5, 30]]],
    },
  },
];
/** r_max=24 + total_area_encoding：24·√(total/200) → 24 / 2.4 / 24·√0.5 */
const BOUND_RADII = [24.0, 2.4, 24.0 * Math.sqrt(100.0 / 200.0)];
const QUALIFIED = {
  values: layoutCase.qualification.values as unknown as GlyphValue[][],
  glyph_feature_ids: layoutCase.qualification.glyph_feature_ids,
};
const CASE6_POLICY = { leader_threshold_px: 0.5 };

/** 测试本地 even-odd 点内判定（代表点 "inside polygon" 断言用）。 */
function pointInPolygon(point: [number, number], rings: number[][][]): boolean {
  const [x, y] = point;
  let inside = false;
  for (const ring of rings) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) {
        inside = !inside;
      }
    }
  }
  return inside;
}

describe("layout case 6 golden parity (layout_determinism)", () => {
  it("glyphs + evidence + digest bit-exact vs fixture", () => {
    const plan = solveDiagramSymbolLayout(FEATURES, QUALIFIED, BOUND_RADII, CASE6_POLICY);
    expect(plan.schema).toBe(layoutCase.layout_plan.schema);
    expect(plan.glyphs).toEqual(layoutCase.layout_plan.glyphs);
    expect(plan.evidence).toEqual(layoutCase.layout_plan.evidence);
    expect(plan.digest).toBe(layoutCase.layout_plan.digest);
  });

  it("deterministic: repeated solve gives identical digest and glyphs", () => {
    const a = solveDiagramSymbolLayout(FEATURES, QUALIFIED, BOUND_RADII, CASE6_POLICY);
    const b = solveDiagramSymbolLayout(FEATURES, QUALIFIED, BOUND_RADII, CASE6_POLICY);
    expect(a.digest).toBe(b.digest);
    expect(a.glyphs).toEqual(b.glyphs);
  });

  it("point anchors pass through (anchor == position, source point)", () => {
    const plan = solveDiagramSymbolLayout(FEATURES, QUALIFIED, BOUND_RADII, CASE6_POLICY);
    const big: LayoutGlyph = plan.glyphs[0];
    expect(big.feature_id).toBe("big");
    expect(big.anchor_source).toBe("point");
    expect(big.anchor).toEqual([104, 30]);
    expect(big.position).toEqual(big.anchor);
    expect(big.displacement_px).toBe(0);
    expect(big.leader_line).toBe(false);
    expect(big.priority_rank).toBe(0);
  });

  it("collision accounting: small glyph at same anchor omitted (not displaced)", () => {
    const plan = solveDiagramSymbolLayout(FEATURES, QUALIFIED, BOUND_RADII, CASE6_POLICY);
    expect(plan.evidence.candidates).toBe(3);
    expect(plan.evidence.placed).toBe(2);
    expect(plan.evidence.omitted_collision).toBe(1);
    expect(plan.evidence.displaced).toBe(0);
    expect(plan.evidence.leader_lines).toBe(0);
    expect(plan.glyphs.map((g) => g.feature_id)).toEqual(["big", "poly"]);
  });

  it("budget accounting: max_glyphs=1 → placed 1 + omitted_budget 2", () => {
    const plan = solveDiagramSymbolLayout(
      FEATURES,
      QUALIFIED,
      BOUND_RADII,
      { ...CASE6_POLICY, max_glyphs: 1 },
    );
    expect(plan.evidence.placed).toBe(1);
    expect(plan.evidence.omitted_budget).toBe(2);
    expect(plan.glyphs[0].feature_id).toBe("big");
  });

  it("small accounting: hide_below_radius_px=3 → omitted_small 1", () => {
    const plan = solveDiagramSymbolLayout(
      FEATURES,
      QUALIFIED,
      BOUND_RADII,
      { ...CASE6_POLICY, hide_below_radius_px: 3 },
    );
    expect(plan.evidence.omitted_small).toBe(1);
    expect(plan.evidence.placed).toBe(2);
  });
});

describe("blocked accounting", () => {
  it("missing geometry / invalid latitude / unsupported type → typed blocked", () => {
    const features: DiagramFeatureInput[] = [
      { type: "Feature" },
      { type: "Feature", geometry: { type: "Point", coordinates: [104, 86] } },
      { type: "Feature", geometry: { type: "MultiPoint", coordinates: [[104, 30]] } },
      { type: "Feature", geometry: { type: "Point", coordinates: [104, 30] } },
    ];
    const plan = solveDiagramSymbolLayout(
      features,
      { values: [[["c", 1]], [["c", 1]], [["c", 1]], [["c", 1]]], glyph_feature_ids: ["a", "b", "c", "ok"] },
      [10, 10, 10, 10],
    );
    expect(plan.evidence.blocked).toBe(3);
    expect(plan.evidence.candidates).toBe(1);
    expect(plan.evidence.blocked_detail.map((d) => d.blocked_reason)).toEqual([
      "missing_geometry",
      "INVALID_COORDINATES",
      "unsupported_geometry_type",
    ]);
    expect(plan.evidence.blocked_truncated).toBe(false);
  });

  it("blocked_detail bounded at 64 with truncation flag", () => {
    const n = 70;
    const features: DiagramFeatureInput[] = Array.from({ length: n }, () => ({ type: "Feature" }));
    const plan = solveDiagramSymbolLayout(
      features,
      {
        values: Array.from({ length: n }, () => [["c", 1]] as GlyphValue[]),
        glyph_feature_ids: Array.from({ length: n }, (_, i) => `f${i}`),
      },
      Array.from({ length: n }, () => 10),
    );
    expect(plan.evidence.blocked).toBe(n);
    expect(plan.evidence.blocked_detail.length).toBe(64);
    expect(plan.evidence.blocked_truncated).toBe(true);
  });
});

describe("anchor strategies", () => {
  it("LineString midpoint = vertex-pair interpolation at half total length", () => {
    const straight = computeFeatureAnchor({
      geometry: { type: "LineString", coordinates: [[100, 30], [101, 30]] },
    });
    expect("anchor" in straight && straight.anchor).toEqual([100.5, 30]);
    expect("anchor_source" in straight && straight.anchor_source).toBe("line_midpoint");
    const lShape = computeFeatureAnchor({
      geometry: { type: "LineString", coordinates: [[0, 0], [2, 0], [2, 2]] },
    });
    expect("anchor" in lShape && lShape.anchor).toEqual([2, 0]);
  });

  it("MultiLineString midpoint walks components cumulatively", () => {
    const anchor = computeFeatureAnchor({
      geometry: { type: "MultiLineString", coordinates: [[[0, 0], [2, 0]], [[10, 10], [10, 12]]] },
    });
    expect("anchor" in anchor && anchor.anchor).toEqual([2, 0]);
  });

  it("polygon representative point is inside + deterministic (U shape multi-span)", () => {
    const rings = [
      [[0, 0], [4, 0], [4, 4], [3, 4], [3, 1], [1, 1], [1, 4], [0, 4], [0, 0]],
    ] as number[][][];
    const feature: DiagramFeatureInput = { geometry: { type: "Polygon", coordinates: rings } };
    const a = computeFeatureAnchor(feature);
    const b = computeFeatureAnchor(feature);
    expect("anchor" in a).toBe(true);
    expect(a).toEqual(b);
    if ("anchor" in a) {
      expect(pointInPolygon(a.anchor, rings)).toBe(true);
      // GEOS InteriorPointArea 同语义：最宽跨段中点（并列取最左）
      expect(a.anchor_source).toBe("polygon_representative_point");
    }
  });

  it("MultiPolygon chooses the largest-area component", () => {
    const anchor = computeFeatureAnchor({
      geometry: {
        type: "MultiPolygon",
        coordinates: [
          [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
          [[[10, 10], [14, 10], [14, 14], [10, 14], [10, 10]]],
        ],
      },
    });
    expect("anchor" in anchor && anchor.anchor).toEqual([12, 12]);
  });

  it("square polygon anchor parity: scanline midpoint == shapely representative_point", () => {
    const anchor = computeFeatureAnchor({ geometry: FEATURES[2].geometry });
    expect("anchor" in anchor && anchor.anchor).toEqual([105.5, 30.5]);
  });

  it("malformed geometry → invalid_geometry (typed)", () => {
    const bad = computeFeatureAnchor({
      geometry: { type: "Point", coordinates: [104] },
    });
    expect("blocked_reason" in bad && bad.blocked_reason).toBe("invalid_geometry");
    const badPoly = computeFeatureAnchor({
      geometry: { type: "Polygon", coordinates: [[[0, 0], [1, "x"], [1, 1], [0, 0]]] },
    });
    expect("blocked_reason" in badPoly && badPoly.blocked_reason).toBe("invalid_geometry");
  });
});

describe("displacement + leader line", () => {
  it("displaced glyph beyond threshold gets leader_line", () => {
    // step = (10+2)/2 = 6；r_eff=36 → 第 6 步（dist 36）恰不碰撞 → displaced
    const features: DiagramFeatureInput[] = [
      { geometry: { type: "Point", coordinates: [104, 30] } },
      { geometry: { type: "Point", coordinates: [104.00001, 30] } },
    ];
    const plan = solveDiagramSymbolLayout(
      features,
      { values: [[["c", 100]], [["c", 50]]], glyph_feature_ids: ["big", "mid"] },
      [24, 10],
      { leader_threshold_px: 0.5 },
    );
    expect(plan.evidence.placed).toBe(2);
    expect(plan.evidence.displaced).toBe(1);
    expect(plan.evidence.leader_lines).toBe(1);
    const mid = plan.glyphs[1];
    expect(mid.leader_line).toBe(true);
    // 恰好逃逸步受浮点影响（36 ± ulp），断言为正向位移而非精确值
    expect(mid.displacement_px).toBeGreaterThan(0);
    expect(mid.position).not.toEqual(mid.anchor);
  });
});

describe("web mercator + policy helpers", () => {
  it("lnglat ↔ world round trip at reference zoom", () => {
    const [x, y] = lnglatToWorld(104, 30);
    expect(x).toBeCloseTo((104 + 180) / 360, 12);
    expect(y).toBeGreaterThan(0);
    expect(y).toBeLessThan(0.5);
    const [lng, lat] = worldToLnglat(x, y);
    expect(Math.round(lng * 1e9) / 1e9).toBe(104);
    expect(Math.round(lat * 1e9) / 1e9).toBe(30);
    expect(worldPxScale(12)).toBe(256 * 4096);
  });

  it("INVALID_COORDINATES for out-of-domain coords", () => {
    expect(() => lnglatToWorld(181, 0)).toThrowError(DiagramSymbolError);
    expect(() => lnglatToWorld(0, 85.06)).toThrowError(DiagramSymbolError);
    expect(() => lnglatToWorld(Number.NaN, 0)).toThrowError(DiagramSymbolError);
  });

  it("glyphMinZoom tiers <4px / <8px with cap 22", () => {
    expect(glyphMinZoom(2, 0)).toBe(2);
    expect(glyphMinZoom(5, 0)).toBe(1);
    expect(glyphMinZoom(9, 0)).toBe(0);
    expect(glyphMinZoom(2, 21)).toBe(22);
  });

  it("policy normalization: defaults + typed rejections", () => {
    const p: NormalizedLayoutPolicy = normalizeLayoutPolicy(null);
    expect(p.max_glyphs).toBe(1000);
    expect(p.collision_padding_px).toBe(2);
    expect(p.leader_threshold_px).toBe(6);
    expect(p.displacement_enabled).toBe(true);
    expect(p.reference_zoom).toBe(12);
    expect(() => normalizeLayoutPolicy({ max_glyphs: 0 })).toThrowError(DiagramSymbolError);
    try {
      normalizeLayoutPolicy({ max_glyphs: 20001 });
      expect.unreachable();
    } catch (err) {
      expect(err).toBeInstanceOf(DiagramSymbolError);
      expect((err as DiagramSymbolError).code).toBe("BUDGET_EXCEEDED");
    }
    expect(() => normalizeLayoutPolicy({ min_zoom_floor: 23 })).toThrowError(DiagramSymbolError);
  });

  it("length mismatch → FIELD_NOT_FOUND; duplicate ids → DUPLICATE_CATEGORY", () => {
    expect(() =>
      solveDiagramSymbolLayout(
        FEATURES.slice(0, 2),
        QUALIFIED,
        BOUND_RADII,
        CASE6_POLICY,
      ),
    ).toThrowError(DiagramSymbolError);
    expect(() =>
      solveDiagramSymbolLayout(
        FEATURES,
        { values: QUALIFIED.values, glyph_feature_ids: ["big", "big", "poly"] },
        BOUND_RADII,
      ),
    ).toThrowError(DiagramSymbolError);
  });
});
