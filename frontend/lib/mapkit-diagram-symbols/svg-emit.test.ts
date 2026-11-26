/**
 * svg-emit.test.ts —— 前端 vector SVG 导出 twin 分支单测。
 * 数值与 oracle 一致（oracle parity 由 oracle.test.ts 锁定）；本文件验证
 * SVG 形状语义（弧/条/分段 path、引线、会计属性、转义与 typed 披露）。
 */
import { describe, expect, it } from "vitest";
import { emitDiagramSymbolLayerSvg } from "./svg-emit";

const IDENTITY: (c: [number, number]) => [number, number] = (c) => [c[0], c[1]];

const BLOCK = {
  schema_version: "diagram_symbol.v1",
  kind: "composition_pie",
  categories: ["小学", "中学"],
  fields: ["primary", "middle"],
  missing_policy: "omit",
  size_policy: { r_max_px: 20 },
  palette_id: "Set1",
};

const FEATURES = [
  { type: "Feature", id: "a",
    geometry: { type: "Point", coordinates: [1, 1] },
    properties: { primary: 30, middle: 20 } },
  { type: "Feature", id: "b",
    geometry: { type: "Point", coordinates: [2, 2] },
    properties: { primary: 10, middle: 30 } },
];

describe("emitDiagramSymbolLayerSvg", () => {
  it("emits per-glyph groups with accounting attrs on layer group", () => {
    const out = emitDiagramSymbolLayerSvg(BLOCK, FEATURES as never, IDENTITY);
    expect("error" in out).toBe(false);
    if ("error" in out) return;
    expect(out.placed).toBe(2);
    expect(out.svg).toContain('data-diagram-symbol-layer="1"');
    expect(out.svg).toContain('data-placed="2"');
    expect(out.svg.match(/data-diagram-symbol="1"/g)?.length).toBe(2);
    expect(out.svg).toContain('data-feature-id="a"');
    expect(out.svg).toContain('data-feature-id="b"');
  });

  it("pie slice arcs project 12-o'clock-clockwise (x=cx+r·sin, y=cy−r·cos)", () => {
    const out = emitDiagramSymbolLayerSvg(BLOCK, FEATURES as never, IDENTITY);
    if ("error" in out) throw new Error("unexpected error");
    // 要素 a：total=60，第一 slice（小学 30）从 0°（12 点钟）起，占 180°
    // 弧起点 = (cx, cy−r)；cx=1, cy=1, r=20 → M 1 1 L 1 -19
    expect(out.svg).toContain("M 1 1 L 1 -19");
  });

  it("escapes category text in feature-id attrs", () => {
    const evil = [{ ...FEATURES[0], id: `<x&"y">` }];
    const out = emitDiagramSymbolLayerSvg(BLOCK, evil as never, IDENTITY);
    if ("error" in out) throw new Error("unexpected error");
    expect(out.svg).toContain('data-feature-id="&lt;x&amp;&quot;y&quot;&gt;"');
    expect(out.svg).not.toContain(`<x&`);
  });

  it("discloses oracle typed errors instead of throwing", () => {
    const neg = [{ ...FEATURES[0], properties: { primary: -5, middle: 2 } }];
    const out = emitDiagramSymbolLayerSvg(BLOCK, neg as never, IDENTITY);
    expect("error" in out).toBe(true);
    if (!("error" in out)) return;
    expect(out.error).toContain("负值");
  });

  it("bars emit rects flipped to screen y", () => {
    const bars = { ...BLOCK, kind: "series_bars", layout: "stacked",
                   size_policy: { h_max_px: 40, bar_w_px: 8, bar_gap_px: 2 } };
    // v_abs_max=30 → px_per_unit=40/30；a.primary=30 → 长度 40 →
    // rect y = cy − 40 = 1 − 40 = -39，height 40（screen y 翻折）
    const out = emitDiagramSymbolLayerSvg(bars, FEATURES as never, IDENTITY);
    if ("error" in out) throw new Error("unexpected error");
    expect(out.svg).toContain(`<rect x="`);
    expect(out.svg).toContain(`y="-39" width="8" height="40"`);
    // b.primary=10 → 高 13.333
    expect(out.svg).toContain(`y="-11.333" width="8" height="13.333"`);
  });

  it("displaced glyphs emit leader lines", () => {
    const near = [
      { type: "Feature", id: "big",
        geometry: { type: "Point", coordinates: [1, 1] },
        properties: { primary: 1000, middle: 1000 } },
      { type: "Feature", id: "small",
        geometry: { type: "Point", coordinates: [1.0001, 1] },
        properties: { primary: 1, middle: 1 } },
    ];
    // 恒定半径 r=10：两锚相距 ~0.001px → 位移步 (10+2)/2=6px，第 4 步
    // (24px ≥ 22px 净距) 放置 → leader（24 > 阈值 6）
    const out = emitDiagramSymbolLayerSvg(
      { ...BLOCK, size_policy: { r_max_px: 10 } },
      near as never, IDENTITY);
    if ("error" in out) throw new Error("unexpected error");
    expect(out.svg).toContain(`<line x1=`);
    expect(out.svg).toContain('data-feature-id="small"');
  });
});
