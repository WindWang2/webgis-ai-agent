/**
 * GOAL 04 M6 —— 多尺度窗口源覆盖（window.source v1.9）前端契约测试。
 *
 * 覆盖三条消费链同语义：
 * 1. carto-ir/representation.ts 归一化携带 source（artifact 窗）；
 * 2. compiler 克隆 artifact 窗子层换用工件源；
 * 3. mapspec-to-svg 导出按活跃窗源覆盖取数 + layer_generalized_artifact
 *    诊断（与后端 multiscale/export_adjudication 孪生）。
 */
import { describe, expect, it } from "vitest";

import { resolveRepresentationPlan, representationWindowAtZoom } from "@/lib/carto-ir/representation";
import { compileMapSpec } from "@/lib/mapspec-compiler/compiler";
import { compileMapSpecToSvg } from "@/lib/mapspec-compiler/mapspec-to-svg";
import type { MapSpec } from "@/lib/mapspec-compiler/types";

function spec(exportZoom: number): MapSpec {
  return {
    version: "1.9",
    view: { center: [104.05, 30.6], zoom: exportZoom },
    sources: {
      roads_src: {
        type: "geojson",
        inlineData: {
          type: "FeatureCollection",
          features: [1, 2, 3, 4, 5].map((i) => ({
            type: "Feature",
            id: `r${i}`,
            properties: {},
            geometry: {
              type: "LineString",
              coordinates: [
                [104 + i * 0.05, 30.5],
                [104 + i * 0.05, 30.7],
              ],
            },
          })),
        },
      },
      "roads__gen@0-8": {
        type: "geojson",
        inlineData: {
          type: "FeatureCollection",
          features: [
            {
              type: "Feature",
              id: "trunk-0",
              properties: {},
              geometry: {
                type: "LineString",
                coordinates: [
                  [104.02, 30.55],
                  [104.02, 30.65],
                ],
              },
            },
          ],
        },
      },
    },
    layers: [
      {
        id: "roads",
        source: "roads_src",
        type: "line",
        paint: { "line-color": "#c00", "line-width": 1.5 },
        representation: {
          windows: [
            {
              min_zoom: 0,
              max_zoom: 8,
              mode: "generalized",
              reason_codes: ["MSP.BAND.WORLD.GENERALIZED"],
              source: "roads__gen@0-8",
            },
            { min_zoom: 8, max_zoom: 24, mode: "raw", reason_codes: [] },
          ],
        },
      } as never,
    ],
  } as never as MapSpec;
}

describe("resolveRepresentationPlan carries window source (v1.9)", () => {
  it("artifact window keeps source; raw window has none", () => {
    const plan = resolveRepresentationPlan(
      (spec(6) as never as { layers: Array<Record<string, unknown>> }).layers[0] as never,
    );
    expect(plan).not.toBeNull();
    const windows = plan!.windows;
    expect(windows).toHaveLength(2);
    expect(windows[0].mode).toBe("generalized");
    expect(windows[0].source).toBe("roads__gen@0-8");
    expect(windows[1].mode).toBe("raw");
    expect(windows[1].source).toBeUndefined();
  });

  it("representationWindowAtZoom returns the active window object", () => {
    const plan = resolveRepresentationPlan(
      (spec(6) as never as { layers: Array<Record<string, unknown>> }).layers[0] as never,
    )!;
    expect(representationWindowAtZoom(plan, 6).source).toBe("roads__gen@0-8");
    expect(representationWindowAtZoom(plan, 12).source).toBeUndefined();
  });

  it("raw window with a stray source key is ignored (fail-safe)", () => {
    const plan = resolveRepresentationPlan({
      representation: {
        windows: [
          { min_zoom: 0, max_zoom: 24, mode: "raw", source: "x" } as never,
        ],
      },
    } as never);
    expect(plan?.windows[0].source).toBeUndefined();
  });
});

describe("compiler clones artifact windows onto the artifact source", () => {
  it("generalized sublayer uses window source; main layer keeps primary", () => {
    const result = compileMapSpec(spec(6));
    const layers = (result.style as { layers: Array<{ id: string; source?: string }> }).layers;
    const sub = layers.find((l) => l.id === "roads__rep0");
    const main = layers.find((l) => l.id === "roads");
    expect(sub).toBeDefined();
    expect(sub!.source).toBe("roads__gen@0-8");
    expect(main!.source).toBe("roads_src");
    // 工件源被挂载（compiler 挂全部 spec.sources）
    expect(
      (result.style as { sources: Record<string, unknown> }).sources["roads__gen@0-8"],
    ).toBeDefined();
  });
});

describe("mapspec-to-svg consumes artifact source at export zoom", () => {
  it("artifact band: renders artifact features + emits diagnostic", () => {
    const diagnostics: Array<[string, string]> = [];
    const svg = compileMapSpecToSvg(spec(6), {
      onDiagnostic: (code: string, detail: string) => diagnostics.push([code, detail]),
    });
    expect(svg).toBeTruthy();
    expect(diagnostics.some(([code]) => code === "layer_generalized_artifact")).toBe(true);
    // 工件源 1 条线（< 主源 5 条）—— 元素数量上界锁住「确实换了源」
    const elemCount = (svg.match(/<(path|line|polyline)\b/g) || []).length;
    expect(elemCount).toBeLessThan(5);
  });

  it("raw band: no override, no diagnostic", () => {
    const diagnostics: Array<[string, string]> = [];
    compileMapSpecToSvg(spec(12), {
      onDiagnostic: (code: string, detail: string) => diagnostics.push([code, detail]),
    });
    expect(diagnostics.some(([code]) => code === "layer_generalized_artifact")).toBe(false);
  });
});
