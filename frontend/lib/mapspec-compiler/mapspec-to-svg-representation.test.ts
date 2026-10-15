import { describe, it, expect } from "vitest";
import { compileMapSpecToSvg } from "./mapspec-to-svg";
import type { MapSpec } from "./types";

/**
 * v1.6（方向 5 / C6）：MapSpec→SVG 导出裁决 parity 测试。
 *
 * 导出 = 固定 zoom 的一次裁决：
 *  - export zoom = options.zoom ?? view.zoom（缺省 8）处求值显隐/结构 zoom
 *    门/表达窗口；门外层跳过 + onDiagnostic 留痕；
 *  - 活跃窗 visual_heatmap（circle 层）→ heatmap 近似分支（半透明圆 +
 *    layer_approximated_heatmap 诊断）；
 *  - layout.visibility=none → 静默跳过（W4 孪生语义：不进导出、无诊断）。
 */

function pointFeatures(n: number) {
  return {
    type: "FeatureCollection" as const,
    features: Array.from({ length: n }, (_, i) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [116.4 + i * 0.01, 39.9] },
      properties: { name: `p${i}` },
    })),
  };
}

interface LayerSpec {
  id: string;
  representation?: MapSpec["layers"][number]["representation"];
  visibility?: MapSpec["layers"][number]["visibility"];
  layout?: Record<string, unknown>;
  paint?: Record<string, unknown>;
}

/** 单源多点 spec；层缺省 circle 型。 */
function specWith(layers: LayerSpec[], view?: { zoom: number }): any {
  return {
    ...(view ? { view } : {}),
    sources: { S: { type: "geojson", inlineData: pointFeatures(2) } },
    layers: layers.map((l) => ({
      type: "circle",
      source: "S",
      paint: { color: "#ab34cd", radius: 6 },
      ...l,
    })),
  };
}

function diagsOf(spec: any, options: Record<string, unknown> = {}) {
  const codes: Array<{ code: string; detail: string }> = [];
  const svg = compileMapSpecToSvg(spec, {
    onDiagnostic: (code: string, detail: string) => codes.push({ code, detail }),
    ...options,
  });
  return { svg, codes };
}

describe("导出裁决：结构 zoom 门", () => {
  it("view.zoom=5 + 层 A visibility.min_zoom=14 → 不渲染 + layer_gated_by_zoom", () => {
    const { svg, codes } = diagsOf(
      specWith([{ id: "A", visibility: { min_zoom: 14 } }], { zoom: 5 }),
    );
    expect(svg).not.toContain("<circle");
    expect(codes.map((c) => c.code)).toContain("layer_gated_by_zoom");
    expect(codes.find((c) => c.code === "layer_gated_by_zoom")!.detail).toContain("layer=A");
    expect(codes.find((c) => c.code === "layer_gated_by_zoom")!.detail).toContain("zoom=5");
  });

  it("同一层 options.zoom=14 → 门内照常渲染（导出 zoom 显式覆盖）", () => {
    const { svg, codes } = diagsOf(specWith([{ id: "A", visibility: { min_zoom: 14 } }], { zoom: 5 }), {
      zoom: 14,
    });
    expect(svg).toContain("<circle");
    expect(codes).toEqual([]);
  });

  it("max_zoom 门半开语义：view.zoom 恰等于 max_zoom → 门外出局", () => {
    const { svg, codes } = diagsOf(
      specWith([{ id: "A", visibility: { max_zoom: 8 } }], { zoom: 8 }),
    );
    expect(svg).not.toContain("<circle");
    expect(codes.map((c) => c.code)).toContain("layer_gated_by_zoom");
  });
});

describe("导出裁决：表达窗 hidden", () => {
  const hiddenThenRaw = {
    windows: [
      { min_zoom: 0, max_zoom: 8, mode: "hidden" as const, reason_codes: ["detail_below_zoom"] },
      { min_zoom: 8, max_zoom: 24, mode: "raw" as const },
    ],
  };

  it("view.zoom=5 落 hidden 窗 → 不渲染 + layer_representation_hidden", () => {
    const { svg, codes } = diagsOf(
      specWith([{ id: "B", representation: hiddenThenRaw }], { zoom: 5 }),
    );
    expect(svg).not.toContain("<circle");
    expect(codes.map((c) => c.code)).toContain("layer_representation_hidden");
    expect(codes.find((c) => c.code === "layer_representation_hidden")!.detail).toContain("layer=B");
  });

  it("options.zoom=10 覆盖到 raw 窗 → 渲染（无诊断）", () => {
    const { svg, codes } = diagsOf(
      specWith([{ id: "B", representation: hiddenThenRaw }], { zoom: 5 }),
      { zoom: 10 },
    );
    expect(svg).toContain("<circle");
    expect(codes).toEqual([]);
  });

  it("view 与 options 均缺 → 缺省 export zoom 8（半开边界恰入 raw 窗）", () => {
    // 无 view.zoom、无 options.zoom：rawZoom=NaN → 缺省 8 → hidden[0,8) 之外
    const { svg, codes } = diagsOf(specWith([{ id: "B", representation: hiddenThenRaw }]));
    expect(svg).toContain("<circle");
    expect(codes).toEqual([]);
  });

  it("hidden 窗与结构门并存时 zoom 门诊断先行（先裁结构门再裁表达窗）", () => {
    // zoom=5 < min_zoom=6：结构门先出局，layer_gated_by_zoom（不是 hidden）
    const { codes } = diagsOf(
      specWith(
        [{ id: "B", representation: hiddenThenRaw, visibility: { min_zoom: 6 } }],
        { zoom: 5 },
      ),
    );
    expect(codes.map((c) => c.code)).toEqual(["layer_gated_by_zoom"]);
  });
});

describe("导出裁决：活跃窗 visual_heatmap（circle 层近似）", () => {
  const heatThenRaw = {
    windows: [
      { min_zoom: 0, max_zoom: 12, mode: "visual_heatmap" as const },
      { min_zoom: 12, max_zoom: 24, mode: "raw" as const },
    ],
  };

  it("zoom=8 落 heatmap 窗 → layer_approximated_heatmap + 半透明圆近似", () => {
    const { svg, codes } = diagsOf(specWith([{ id: "C", representation: heatThenRaw }]), {
      targetDpi: 72,
    });
    expect(codes.some((c) => c.code === "layer_approximated_heatmap")).toBe(true);
    // heatmap 分支：r 取 paint.radius（6×dpiScale=6）、fill=color、
    // fill-opacity 缺省 0.6（区别于 circle 分支的缺省 1）
    expect(svg).toContain('r="6" fill="#ab34cd" fill-opacity="0.6"');
  });

  it("zoom=14 落 raw 窗 → 普通 circle 渲染、无 heatmap 近似诊断", () => {
    const { svg, codes } = diagsOf(specWith([{ id: "C", representation: heatThenRaw }]), {
      targetDpi: 72,
      zoom: 14,
    });
    expect(codes).toEqual([]);
    // circle 分支缺省 fill-opacity=1
    expect(svg).toContain('fill-opacity="1"');
    expect(svg).not.toContain('fill-opacity="0.6"');
  });

  it("活跃窗 point_cluster → 按声明层型渲染（circle 近似、无 heatmap 近似诊断）", () => {
    const plan = {
      windows: [
        { min_zoom: 0, max_zoom: 14, mode: "point_cluster" as const },
        { min_zoom: 14, max_zoom: 24, mode: "raw" as const },
      ],
    };
    const { svg, codes } = diagsOf(specWith([{ id: "C", representation: plan }]), {
      targetDpi: 72,
    });
    expect(codes).toEqual([]);
    expect(svg).toContain('fill-opacity="1"');
  });
});

describe("导出裁决：用户显隐（layout.visibility=none）", () => {
  it("静默跳过：无诊断、无产物", () => {
    const { svg, codes } = diagsOf(
      specWith([{ id: "D", layout: { visibility: "none" }, representation: {
        windows: [
          { min_zoom: 0, max_zoom: 8, mode: "hidden" },
          { min_zoom: 8, max_zoom: 24, mode: "raw" },
        ],
      } }]),
    );
    expect(svg).not.toContain("<circle");
    expect(codes).toEqual([]);
  });
});
