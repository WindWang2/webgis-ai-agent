/**
 * integration.test.ts —— MapSpec 层 → live 图符层接线面单测。
 *
 * oracle 全链在 oracle/layout 测试已做位级 parity；本文件验证接线语义：
 * 色板权威镜像、资格抽取 omit 会计、attach/detach 生命周期与诚实披露。
 */
import { describe, expect, it } from "vitest";
import {
  attachDiagramSymbolLayer,
  buildGlyphInstancesForLayer,
  detachDiagramSymbolLayer,
  qualifyFeatureValues,
  resolvePaletteColors,
  QUALITATIVE_PALETTES,
} from "./integration";

const BLOCK = {
  schema_version: "diagram_symbol.v1",
  kind: "composition_pie",
  categories: ["小学", "中学"],
  fields: ["primary", "middle"],
  missing_policy: "omit",
  size_policy: { r_max_px: 24 },
  palette_id: "Set1",
};

const FEATURES = [
  { type: "Feature", id: "a",
    geometry: { type: "Point", coordinates: [104.0, 30.0] },
    properties: { primary: 30, middle: 20 } },
  { type: "Feature", id: "b",
    geometry: { type: "Point", coordinates: [104.01, 30.0] },
    properties: { primary: 5, middle: null } },
  { type: "Feature", id: "c",
    geometry: { type: "Point", coordinates: [104.02, 30.0] },
    properties: { primary: null, middle: 8 } },
];

function mockMap() {
  const layers = new Set<string>();
  return {
    layers,
    getLayer: (id: string) => (layers.has(id) ? {} : undefined),
    addLayer: (def: { id: string }) => { layers.add(def.id); },
    removeLayer: (id: string) => { layers.delete(id); },
    getZoom: () => 12,
    project: () => ({ x: 100, y: 100 }),
    getCanvas: () => ({ clientWidth: 800, clientHeight: 600 }),
  } as never as Parameters<typeof attachDiagramSymbolLayer>[0];
}

describe("palette authority mirror", () => {
  it("mirrors backend palettes.py bit-exactly", () => {
    expect(QUALITATIVE_PALETTES.Set1[0]).toBe("#e41a1c");
    expect(QUALITATIVE_PALETTES.Set1).toHaveLength(9);
    expect(QUALITATIVE_PALETTES.Set2).toHaveLength(8);
    expect(QUALITATIVE_PALETTES.Dark2).toHaveLength(8);
    expect(QUALITATIVE_PALETTES.Pastel1).toHaveLength(9);
  });
  it("falls back to Set1 for unknown palette", () => {
    expect(resolvePaletteColors("NotARealPalette")).toEqual(QUALITATIVE_PALETTES.Set1);
    expect(resolvePaletteColors(null)).toEqual(QUALITATIVE_PALETTES.Set1);
  });
});

describe("qualifyFeatureValues", () => {
  it("omits missing with per-category accounting", () => {
    const q = qualifyFeatureValues(FEATURES, ["小学", "中学"], ["primary", "middle"]);
    expect(q.values).toHaveLength(3);
    expect(q.glyph_feature_ids).toEqual(["a", "b", "c"]);
    expect(q.missing_by_category["中学"]).toBe(1); // b.middle null
    expect(q.missing_by_category["小学"]).toBe(1); // c.primary null
    // b 行只有小学；c 行只有中学
    expect(q.values[1].map((v) => v[0])).toEqual(["小学"]);
    expect(q.values[2].map((v) => v[0])).toEqual(["中学"]);
  });
});

describe("buildGlyphInstancesForLayer", () => {
  it("produces instances with palette/anchor/leader fields", () => {
    const built = buildGlyphInstancesForLayer(BLOCK, FEATURES as never);
    expect(built.instances).toHaveLength(3);
    for (const inst of built.instances) {
      expect(inst.palette).toEqual(QUALITATIVE_PALETTES.Set1);
      expect(Array.isArray(inst.anchor)).toBe(true);
      expect(typeof inst.radiusPx).toBe("number");
      expect(inst.geometry).toBeTruthy();
    }
    const ev = built.evidence as { placed?: number; candidates?: number };
    expect(ev.placed).toBe(3);
  });
  it("rejects negative composition values with typed error (parity with backend)", () => {
    const neg = [{ ...FEATURES[0], properties: { primary: -1, middle: 2 } }];
    expect(() => buildGlyphInstancesForLayer(BLOCK, neg as never)).toThrow(/NEGATIVE_VALUE|负值/);
  });
});

describe("attach/detach lifecycle", () => {
  it("attaches custom layer with LAYER_ID_SUFFIX and registers remount", () => {
    const map = mockMap();
    const layer = { id: "lyr-1", source: "s1", diagram_symbol: BLOCK };
    const result = attachDiagramSymbolLayer(
      map, layer as never, { features: FEATURES } as never);
    expect(result.ok).toBe(true);
    if (result.ok) expect(result.count).toBe(3);
    expect(map.getLayer("lyr-1-diagram-symbols")).toBeTruthy();
    // 幂等重挂
    attachDiagramSymbolLayer(map, layer as never, { features: FEATURES } as never);
    expect(map.getLayer("lyr-1-diagram-symbols")).toBeTruthy();
  });
  it("discloses unavailable source features honestly", () => {
    const map = mockMap();
    const layer = { id: "lyr-2", source: "s1", diagram_symbol: BLOCK };
    const result = attachDiagramSymbolLayer(map, layer as never, { features: [] } as never);
    expect(result.ok).toBe(false);
    if (!result.ok) expect(result.code).toBe("DIAGRAM_SOURCE_FEATURES_UNAVAILABLE");
    expect(map.getLayer("lyr-2-diagram-symbols")).toBeFalsy();
  });
  it("detaches with the base layer lifecycle", () => {
    const map = mockMap();
    const layer = { id: "lyr-3", source: "s1", diagram_symbol: BLOCK };
    attachDiagramSymbolLayer(map, layer as never, { features: FEATURES } as never);
    expect(map.getLayer("lyr-3-diagram-symbols")).toBeTruthy();
    detachDiagramSymbolLayer(map, "lyr-3");
    expect(map.getLayer("lyr-3-diagram-symbols")).toBeFalsy();
  });
  it("detach without attach is a no-op", () => {
    const map = mockMap();
    expect(() => detachDiagramSymbolLayer(map, "never-attached")).not.toThrow();
  });
});
