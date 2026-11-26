/**
 * oracle.test.ts —— oracle 孪生与 golden fixtures（cases 1-5）的逐位 parity，
 * 加 typed 错误合同（NEGATIVE_VALUE / ZERO_TOTAL / EXTREME_VALUE_RANGE 等）。
 */

import { describe, expect, it } from "vitest";
import fixturesJson from "./fixtures/oracle-fixtures.json";
import {
  DiagramSymbolError,
  buildGlyphGeometry,
  computeGlyphScale,
  geometryDigest,
  glyphBoundRadius,
  normalizeSizePolicy,
  pythonHypot2,
  pythonRound,
  quantize3,
  type GlyphValue,
  type SizePolicyInput,
  type ValueRow,
} from "./oracle";

interface FixtureOracleCase {
  name: string;
  kind: string;
  layout?: string;
  rows: [string, number][][];
  scale: Record<string, number>;
  size_policy: Record<string, number | boolean>;
  glyphs: unknown[];
  bound_radii: number[];
}

const fixtureCases = (fixturesJson as { cases: unknown[] }).cases;
const oracleCases = fixtureCases.slice(0, 5) as FixtureOracleCase[];

function captureCode(fn: () => unknown): string {
  try {
    fn();
  } catch (err) {
    if (err instanceof DiagramSymbolError) return err.code;
    throw err;
  }
  throw new Error("expected DiagramSymbolError but nothing thrown");
}

describe("oracle fixtures parity (cases 1-5)", () => {
  for (const c of oracleCases) {
    it(`case ${c.name}: scale + glyphs + bound_radii bit-exact`, () => {
      const sp = normalizeSizePolicy(c.kind, c.size_policy as SizePolicyInput);
      const scale = computeGlyphScale(c.kind, c.rows as unknown as ValueRow[], sp);
      expect(scale).toEqual(c.scale);
      const glyphs = (c.rows as unknown as GlyphValue[][]).map((row) =>
        buildGlyphGeometry(c.kind, row, scale, c.size_policy as SizePolicyInput, {
          layout: c.layout,
        }),
      );
      expect(glyphs).toEqual(c.glyphs);
      expect(glyphs.map((g) => glyphBoundRadius(g))).toEqual(c.bound_radii);
    });
  }
});

describe("oracle quantize + error contracts", () => {
  it("pythonHypot2 bit-matches Python 3.12 math.hypot（含 subnormal / near-overflow）", () => {
    // 期望值全部来自 venv Python 3.12.15 math.hypot 的 repr 输出
    expect(pythonHypot2(4, 24)).toBe(24.331050121192877);
    expect(pythonHypot2(14, 24)).toBe(27.784887978899608);
    expect(pythonHypot2(4, 40)).toBe(40.19950248448356);
    expect(pythonHypot2(14, 40)).toBe(42.37924020083418);
    expect(pythonHypot2(0, 0)).toBe(0);
    expect(pythonHypot2(5e-324, 5e-324)).toBe(5e-324);
    expect(pythonHypot2(1e-320, 3e-320)).toBe(3.162e-320);
    expect(pythonHypot2(1e300, 1e300)).toBe(1.4142135623730952e300);
    expect(Number.isNaN(pythonHypot2(Number.NaN, 1))).toBe(true);
  });

  it("quantize3 rounds to 3 decimals and normalizes -0", () => {
    expect(quantize3(1 / 6)).toBe(0.167);
    expect(quantize3(5 / 60)).toBe(0.083);
    expect(quantize3(-0.0002)).toBe(0);
    expect(Object.is(quantize3(-0.0002), -0)).toBe(false);
    expect(quantize3(16.970562748477143)).toBe(16.971);
  });

  it("pythonRound matches Python round() at binary half-way boundaries", () => {
    // Python round((24.333+2)/2, 3)：双精度精确值低于十进制中点 → 13.166
    // （Math.round(v*1000)/1000 会双重舍入到 13.167 —— 随机语料捕获）
    const displaced = (24.333 + 2) / 2;
    expect(pythonRound(displaced, 3)).toBe(13.166);
    // round(2.675, 2) = 2.67（二进制值低于中点）
    expect(pythonRound(2.675, 2)).toBe(2.67);
    // 精确 half-even：round(0.125, 2) = 0.12（0.125 可精确表示，平局取偶）
    expect(pythonRound(0.125, 2)).toBe(0.12);
    expect(pythonRound(0.375, 2)).toBe(0.38);
    // 非边界常规值
    expect(pythonRound(1 / 6, 3)).toBe(0.167);
    expect(pythonRound(-1 / 6, 3)).toBe(-0.167);
    expect(pythonRound(104.00000000000006, 9)).toBe(104);
  });

  it("composition negative value throws NEGATIVE_VALUE", () => {
    const values: GlyphValue[] = [["a", -1], ["b", 2]];
    const code = captureCode(() =>
      buildGlyphGeometry("composition_pie", values, { r_max_px: 32 }, { r_max_px: 32 }),
    );
    expect(code).toBe("NEGATIVE_VALUE");
  });

  it("segmented negative value throws NEGATIVE_VALUE (bars allow negatives)", () => {
    const values: GlyphValue[] = [["a", -1], ["b", 2]];
    expect(
      captureCode(() =>
        buildGlyphGeometry("segmented_proportional", values, { r_max_px: 32, v_ref: 2 }, { r_max_px: 32 }),
      ),
    ).toBe("NEGATIVE_VALUE");
    // series_bars 允许负值（0 基线长度语义）
    expect(() =>
      buildGlyphGeometry(
        "series_bars",
        values,
        { px_per_unit: 2, bar_w_px: 8 },
        { h_max_px: 40, bar_w_px: 8 },
      ),
    ).not.toThrow();
  });

  it("zero total composition throws ZERO_TOTAL", () => {
    const values: GlyphValue[] = [["a", 0], ["b", 0]];
    expect(
      captureCode(() =>
        buildGlyphGeometry("composition_pie", values, { r_max_px: 32 }, { r_max_px: 32 }),
      ),
    ).toBe("ZERO_TOTAL");
  });

  it("all-zero bars scale throws ZERO_TOTAL", () => {
    const rows: ValueRow[] = [[["a", 0], ["b", 0]]];
    expect(
      captureCode(() =>
        computeGlyphScale("series_bars", rows, { h_max_px: 40, bar_w_px: 8, bar_gap_px: 1 }),
      ),
    ).toBe("ZERO_TOTAL");
  });

  it("EXTREME_VALUE_RANGE rejects max/min > 10000 (composition + bars)", () => {
    const rows: ValueRow[] = [[["a", 1], ["b", 10000.5]]];
    expect(
      captureCode(() =>
        computeGlyphScale("composition_pie", rows, { r_max_px: 32 }),
      ),
    ).toBe("EXTREME_VALUE_RANGE");
    const barRows: ValueRow[] = [[["a", 0.001], ["b", 20]]];
    expect(
      captureCode(() =>
        computeGlyphScale("series_bars", barRows, { h_max_px: 40, bar_w_px: 8, bar_gap_px: 1 }),
      ),
    ).toBe("EXTREME_VALUE_RANGE");
  });

  it("NON_FINITE_VALUE rejects NaN/Inf values", () => {
    const values = [["a", Number.NaN], ["b", 1]] as unknown as GlyphValue[];
    expect(
      captureCode(() =>
        buildGlyphGeometry("composition_pie", values, { r_max_px: 32 }, { r_max_px: 32 }),
      ),
    ).toBe("NON_FINITE_VALUE");
    const infValues = [["a", Number.POSITIVE_INFINITY], ["b", 1]] as unknown as GlyphValue[];
    expect(
      captureCode(() =>
        buildGlyphGeometry("composition_pie", infValues, { r_max_px: 32 }, { r_max_px: 32 }),
      ),
    ).toBe("NON_FINITE_VALUE");
  });

  it("FIELD_NOT_NUMERIC rejects bool/string values (JSON 防御)", () => {
    const values = [["a", true], ["b", 1]] as unknown as GlyphValue[];
    expect(
      captureCode(() =>
        buildGlyphGeometry("composition_pie", values, { r_max_px: 32 }, { r_max_px: 32 }),
      ),
    ).toBe("FIELD_NOT_NUMERIC");
  });

  it("INVALID_SIZE_POLICY / INNER_RADIUS_OUT_OF_RANGE / UNKNOWN_KIND / UNKNOWN_LAYOUT", () => {
    expect(
      captureCode(() => normalizeSizePolicy("composition_pie", { r_max_px: 120.5 })),
    ).toBe("INVALID_SIZE_POLICY");
    expect(
      captureCode(() => normalizeSizePolicy("composition_donut", { inner_radius_ratio: 1.0 })),
    ).toBe("INNER_RADIUS_OUT_OF_RANGE");
    expect(
      captureCode(() => normalizeSizePolicy("nope", {})),
    ).toBe("UNKNOWN_KIND");
    expect(
      captureCode(() =>
        buildGlyphGeometry("series_bars", [["a", 1], ["b", 2]], { px_per_unit: 1 }, { h_max_px: 40 }, {
          layout: "spiral",
        }),
      ),
    ).toBe("UNKNOWN_LAYOUT");
  });

  it("total_area_encoding: radius ∝ sqrt(total/v_ref) 并 clamp 到 r_min（披露 clamped）", () => {
    // total=25, v_ref=1000 → raw = 32·sqrt(25/1000) ≈ 5.06 < r_min 6 → clamp
    const glyph = buildGlyphGeometry(
      "composition_pie",
      [["a", 10], ["b", 15]],
      { r_max_px: 32, v_ref: 1000, total_area_encoding: 1, r_min_px: 6 },
      { r_max_px: 32, total_area_encoding: true, r_min_px: 6 },
    );
    expect(glyph.kind).toBe("composition_pie");
    const comp = glyph as unknown as {
      radius_px: number; radius_clamped_to_min?: boolean; total: number;
    };
    expect(comp.radius_px).toBe(6);
    expect(comp.radius_clamped_to_min).toBe(true);
    expect(comp.total).toBe(25);
  });

  it("donut emits inner_radius_ratio/inner_radius_px", () => {
    const glyph = buildGlyphGeometry(
      "composition_donut",
      [["a", 70], ["b", 30]],
      { r_max_px: 40 },
      { r_max_px: 40, inner_radius_ratio: 0.55 },
    );
    if (glyph.kind !== "composition_donut") throw new Error("expected donut");
    expect(glyph.inner_radius_ratio).toBe(0.55);
    expect(glyph.inner_radius_px).toBe(22);
  });

  it("geometryDigest is deterministic and value-sensitive", () => {
    const g1 = buildGlyphGeometry(
      "composition_pie",
      [["a", 1], ["b", 2]],
      { r_max_px: 32 },
      { r_max_px: 32 },
    );
    const g2 = buildGlyphGeometry(
      "composition_pie",
      [["a", 1], ["b", 2]],
      { r_max_px: 32 },
      { r_max_px: 32 },
    );
    const g3 = buildGlyphGeometry(
      "composition_pie",
      [["a", 1], ["b", 3]],
      { r_max_px: 32 },
      { r_max_px: 32 },
    );
    expect(geometryDigest([g1])).toBe(geometryDigest([g2]));
    expect(geometryDigest([g1])).not.toBe(geometryDigest([g3]));
  });

  it("glyphBoundRadius unknown kind throws UNKNOWN_KIND", () => {
    expect(
      captureCode(() => glyphBoundRadius({ kind: "mystery" } as never)),
    ).toBe("UNKNOWN_KIND");
  });
});
