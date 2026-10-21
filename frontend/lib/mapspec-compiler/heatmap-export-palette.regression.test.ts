/**
 * H02 — 他属 issue 回归契约（#1747 / #1726，H02 只锁定契约不修复）。
 *
 * 这些 xfail（非 strict）契约把已知缺陷的**期望行为**固化为可执行文档：
 * 归属修复 PR 落地后 XPASS 提醒移除标记；H02 不动其实现文件。
 */
import { describe, expect, it } from "vitest";

import { compileMapSpec } from "@/lib/mapspec-compiler/compiler";
import type { MapSpec } from "@/lib/mapspec-compiler/types.generated";

const HEAT_PAINT = {
  "heatmap-color": [
    "interpolate", ["linear"], ["heatmap-density"],
    0, "rgba(0,0,255,0)", 0.5, "#00ff41", 1, "#ff2d55",
  ],
  "heatmap-intensity": 1.2,
  "heatmap-weight": 0.8,
  "heatmap-radius": 18,
  "heatmap-opacity": 0.9,
};

function heatmapWindowSpec(): MapSpec {
  return {
    version: "1.6",
    view: { center: [104.06, 30.66], zoom: 12 },
    layers: [
      {
        id: "poi",
        type: "circle",
        source: "s1",
        paint: { color: "#3388aa" },
        representation: {
          windows: [
            { index: 0, minZoom: 0, maxZoom: 10, mode: "raw", reasonCodes: ["declared"] },
            { index: 1, minZoom: 10, maxZoom: 20, mode: "visual_heatmap", reasonCodes: ["declared"] },
          ],
        },
      },
    ],
    sources: {
      s1: {
        type: "geojson",
        data: {
          type: "FeatureCollection",
          features: Array.from({ length: 20 }, (_, i) => ({
            type: "Feature",
            geometry: { type: "Point", coordinates: [104.0 + i * 0.01, 30.66] },
            properties: {},
          })),
        },
      },
    },
  } as unknown as MapSpec;
}

describe("H02 regression contracts (owned by other issues)", () => {
  it.fails("compileMapSpec keeps native heatmap-* paint keys on the heatmap window sublayer", async () => {
    // #1747 item 1（前端导出链归属）：headless 编译的 visual_heatmap 窗
    // 子层当前只发 heatmap-radius（compiler.ts heatmap 窗分支），live 侧
    // heatmapOnlyPaint 保留全部 heatmap-* 键 → 导出掉回 MapLibre 默认蓝色
    // 色带。期望：子层 paint 至少保留 heatmap-color / heatmap-intensity /
    // heatmap-opacity（当原层 paint 携带时）。
    // it.fails = strict xfail：#1747 修复落地后本用例 XPASS 亮灯，
    // 提醒移除 .fails 标记（H02 不动其实现文件）。
    const spec = heatmapWindowSpec();
    (spec.layers[0] as unknown as Record<string, unknown>).paint = {
      ...HEAT_PAINT,
    };
    const result = compileMapSpec(spec);
    const style = result.style as unknown as {
      layers: Array<{ id: string; type: string; paint?: Record<string, unknown> }>;
    };
    const heatSub = style.layers.find(
      (l) => l.type === "heatmap" && String(l.id).includes("poi"),
    );
    expect(heatSub).toBeDefined();
    expect(Object.keys(heatSub!.paint ?? {})).toEqual(
      expect.arrayContaining(["heatmap-color", "heatmap-intensity", "heatmap-opacity"]),
    );
  });
});
