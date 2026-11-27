/**
 * vertices.test.ts —— 三角化纯函数单元：pie fan / donut strip / bar quad
 * 数量与 y-up 翻转、hex 颜色解码、palette 循环、kind 分派。
 */

import { describe, expect, it } from "vitest";
import {
  arcSegmentCount,
  barQuad,
  donutRingStrip,
  glyphAngleUnitVector,
  parseHexColor,
  pieSliceFan,
  triangulateGlyph,
  type RgbaColor,
} from "./vertices";
import type { SegmentedGlyph, SeriesBarsGlyph } from "./oracle";

const RED: RgbaColor = { r: 1, g: 0, b: 0, a: 1 };
const BLUE: RgbaColor = { r: 0, g: 0, b: 1, a: 1 };

describe("color decode", () => {
  it("hex → rgb normalized 0..1", () => {
    expect(parseHexColor("#ff0000")).toEqual({ r: 1, g: 0, b: 0, a: 1 });
    expect(parseHexColor("#0a8bff")).toEqual({ r: 10 / 255, g: 139 / 255, b: 1, a: 1 });
    expect(parseHexColor("#f00")).toEqual(RED);
    expect(parseHexColor("00ff88")).toEqual({ r: 0, g: 1, b: 136 / 255, a: 1 });
  });

  it("invalid hex throws", () => {
    expect(() => parseHexColor("#12345")).toThrowError(/invalid hex/);
    expect(() => parseHexColor("zzzzzz")).toThrowError(/invalid hex/);
    expect(() => parseHexColor("")).toThrowError(/invalid hex/);
  });
});

describe("angle convention (0° = 12 点钟, clockwise, screen y-down)", () => {
  it("unit vectors", () => {
    expect(glyphAngleUnitVector(0)[0]).toBeCloseTo(0, 12);
    expect(glyphAngleUnitVector(0)[1]).toBeCloseTo(-1, 12);
    expect(glyphAngleUnitVector(90)[0]).toBeCloseTo(1, 12);
    expect(glyphAngleUnitVector(90)[1]).toBeCloseTo(0, 12);
    expect(glyphAngleUnitVector(180)[1]).toBeCloseTo(1, 12);
    expect(glyphAngleUnitVector(270)[0]).toBeCloseTo(-1, 12);
  });
});

describe("arc segmentation", () => {
  it("clamp(ceil(span/6), 2, 48)", () => {
    expect(arcSegmentCount(90)).toBe(15);
    expect(arcSegmentCount(13)).toBe(3);
    expect(arcSegmentCount(3)).toBe(2);
    expect(arcSegmentCount(12)).toBe(2);
    expect(arcSegmentCount(360)).toBe(48);
  });
});

describe("pie slice fan", () => {
  it("90° slice → 15 triangles → 45 vertices, center at origin", () => {
    const tri = pieSliceFan(30, 0, 90, RED);
    expect(tri.vertexCount).toBe(45);
    expect(tri.vertices.length).toBe(45 * 6);
    // 第一个三角形：center(0,0) → arc(0°)=(0,-30) → arc(6°)
    expect(tri.vertices[0]).toBe(0);
    expect(tri.vertices[1]).toBe(0);
    expect(tri.vertices[2]).toBe(1);
    expect(tri.vertices[6]).toBeCloseTo(0, 12);
    expect(tri.vertices[7]).toBeCloseTo(-30, 12);
    // 弧点在半径 30 上
    const r = Math.hypot(tri.vertices[6], tri.vertices[7]);
    expect(r).toBeCloseTo(30, 12);
  });

  it("full circle → 48 segments clamped", () => {
    const tri = pieSliceFan(20, 0, 360, RED);
    expect(tri.vertexCount).toBe(48 * 3);
  });
});

describe("donut ring strip", () => {
  it("252° ring → 42 segments → 84 triangles, both radii present", () => {
    const tri = donutRingStrip(40, 22, 0, 252, BLUE);
    expect(tri.vertexCount).toBe(42 * 6);
    const ys = Array.from(tri.vertices).filter((_, i) => i % 6 === 1);
    // angle 0 是 strip 首段起点：外弧 y=-40、内弧 y=-22 逐位出现
    expect(ys.some((y) => Math.abs(y + 40) < 1e-9)).toBe(true);
    expect(ys.some((y) => Math.abs(y + 22) < 1e-9)).toBe(true);
    expect(Math.min(...ys)).toBeGreaterThanOrEqual(-40 - 1e-9);
  });
});

describe("bar quad + glyph-local y-up flip", () => {
  it("positive local y renders above baseline (screen negative y)", () => {
    const tri = barQuad(-4, 4, 0, 20, RED);
    expect(tri.vertexCount).toBe(6);
    expect(tri.vertices.length).toBe(36);
    const ys = Array.from(tri.vertices).filter((_, i) => i % 6 === 1);
    expect(new Set(ys)).toEqual(new Set([0, -20]));
    const xs = Array.from(tri.vertices).filter((_, i) => i % 6 === 0);
    expect(new Set(xs)).toEqual(new Set([-4, 4]));
  });

  it("negative local y (below baseline) renders positive screen y", () => {
    const tri = barQuad(-4, 4, -7, 0, RED);
    const ys = Array.from(tri.vertices).filter((_, i) => i % 6 === 1);
    expect(new Set(ys)).toEqual(new Set([0, 7]));
  });
});

describe("triangulateGlyph dispatch", () => {
  it("composition_pie fans per slice with palette cycling", () => {
    const glyph = {
      kind: "composition_pie" as const,
      radius_px: 32,
      total: 60,
      slices: [
        { category: "a", value: 30, share: 0.5, start_angle_deg: 0, end_angle_deg: 180 },
        { category: "b", value: 20, share: 0.333, start_angle_deg: 180, end_angle_deg: 300 },
        { category: "c", value: 10, share: 0.167, start_angle_deg: 300, end_angle_deg: 360 },
      ],
      zero_slices: 0,
    };
    // slice 1: 180° → 30 seg → 90 verts; slice 2: 120° → 20 seg → 60; slice 3: 60° → 10 seg → 30
    const tri = triangulateGlyph(glyph, ["#ff0000", "#0000ff"]);
    expect(tri.vertexCount).toBe(90 + 60 + 30);
    // 第三个 slice（index 2）循环回 palette[0]（红色）
    const slice3Start = (90 + 60) * 6;
    expect(tri.vertices[slice3Start + 2]).toBe(1);
    expect(tri.vertices[slice3Start + 3]).toBe(0);
    expect(tri.vertices[slice3Start + 4]).toBe(0);
  });

  it("composition_donut uses inner radius (strip, not fan)", () => {
    const glyph = {
      kind: "composition_donut" as const,
      radius_px: 40,
      total: 100,
      slices: [
        { category: "a", value: 70, share: 0.7, start_angle_deg: 0, end_angle_deg: 252 },
        { category: "b", value: 30, share: 0.3, start_angle_deg: 252, end_angle_deg: 360 },
      ],
      zero_slices: 0,
      inner_radius_ratio: 0.55,
      inner_radius_px: 22,
    };
    // 252° → 42 seg；108° → 18 seg → (42 + 18) * 6 verts
    const tri = triangulateGlyph(glyph, ["#112233", "#445566"]);
    expect(tri.vertexCount).toBe((42 + 18) * 6);
  });

  it("series_bars quads with zero-value segment skipped", () => {
    const glyph: SeriesBarsGlyph = {
      kind: "series_bars",
      layout: "stacked",
      baseline_y: 0,
      extent_px: { top: 24, bottom: -7 },
      segments: [
        { category: "a", value: 0, x0: -4, x1: 4, y0: 0, y1: 0 },
        { category: "b", value: 12, x0: -4, x1: 4, y0: 0, y1: 24 },
        { category: "c", value: -3.5, x0: -4, x1: 4, y0: -7, y1: 0 },
      ],
      zero_values: 1,
    };
    const tri = triangulateGlyph(glyph, ["#ff0000", "#00ff00", "#0000ff"]);
    expect(tri.vertexCount).toBe(12);
    // y-up flip：y1=24 → 屏幕 -24
    const ys = Array.from(tri.vertices).filter((_, i) => i % 6 === 1);
    expect(Math.min(...ys)).toBe(-24);
    expect(Math.max(...ys)).toBe(7);
  });

  it("segmented wedges use per-segment radius, zero-radius skipped", () => {
    const glyph: SegmentedGlyph = {
      kind: "segmented_proportional",
      slot_deg: 180,
      radius_px: 30,
      segments: [
        { category: "a", value: 10, start_angle_deg: 0, end_angle_deg: 180, radius_px: 15, area_px2: 353.429 },
        { category: "b", value: 40, start_angle_deg: 180, end_angle_deg: 360, radius_px: 30, area_px2: 1413.717 },
        { category: "z", value: 0, start_angle_deg: 360, end_angle_deg: 540, radius_px: 0, area_px2: 0 },
      ],
      zero_segments: 1,
    };
    // 每个 180° wedge → 30 seg → 90 verts；零半径段跳过
    const tri = triangulateGlyph(glyph, ["#ff0000", "#0000ff", "#00ff00"]);
    expect(tri.vertexCount).toBe(180);
    const rs = Array.from(tri.vertices)
      .filter((_, i) => i % 6 === 0)
      .map((x) => (x === 0 ? null : Math.abs(x)));
    expect(Math.max(...(rs.filter((v) => v !== null) as number[]))).toBeCloseTo(30, 12);
  });
});
