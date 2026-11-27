/**
 * layer.test.ts —— 自定义图层工厂冒烟测试（无 WebGL：不触发 onAdd/render
 * 的真实 GL 路径；render 在 onAdd 之前调用必须安全 no-op）。
 */

import { describe, expect, it } from "vitest";
import { createDiagramSymbolCustomLayer } from "./layer";
import { LAYER_ID_SUFFIX } from "./index";
import type { DiagramGlyphInstance } from "./layer";
import type { DiagramGlyph } from "./oracle";

const INSTANCE: DiagramGlyphInstance = {
  position: [104, 30],
  geometry: {
    kind: "composition_pie",
    radius_px: 32,
    total: 60,
    slices: [
      { category: "a", value: 30, share: 0.5, start_angle_deg: 0, end_angle_deg: 180 },
      { category: "b", value: 20, share: 0.333, start_angle_deg: 180, end_angle_deg: 300 },
      { category: "c", value: 10, share: 0.167, start_angle_deg: 300, end_angle_deg: 360 },
    ],
    zero_slices: 0,
  } as DiagramGlyph,
  palette: ["#ff0000", "#00ff00", "#0000ff"],
  anchor: [104, 30],
  leaderLine: false,
  displacementPx: 0,
  radiusPx: 32,
};

describe("createDiagramSymbolCustomLayer", () => {
  it("exposes CustomLayerInterface shape + glyph setters", () => {
    const layer = createDiagramSymbolCustomLayer({ id: `plan${LAYER_ID_SUFFIX}` });
    expect(layer.id).toBe("plan-diagram-symbols");
    expect(layer.type).toBe("custom");
    expect(layer.renderingMode).toBe("2d");
    expect(typeof layer.onAdd).toBe("function");
    expect(typeof layer.render).toBe("function");
    expect(typeof layer.onRemove).toBe("function");
    expect(layer.getGlyphs()).toEqual([]);
    layer.setGlyphs([INSTANCE]);
    expect(layer.getGlyphs()).toEqual([INSTANCE]);
    // setter 复制入参：外部数组变更不影响图层状态
    const external = [INSTANCE];
    layer.setGlyphs(external);
    external.length = 0;
    expect(layer.getGlyphs().length).toBe(1);
  });

  it("default id + leader color option", () => {
    const layer = createDiagramSymbolCustomLayer({ glyphs: [INSTANCE], leaderLineColor: "#ff6b00" });
    expect(layer.id).toBe("diagram-symbols");
    expect(layer.getGlyphs()).toEqual([INSTANCE]);
  });

  it("render before onAdd is a safe no-op", () => {
    const layer = createDiagramSymbolCustomLayer({ glyphs: [INSTANCE] });
    const stubGl = {} as unknown as WebGL2RenderingContext;
    const stubOptions = {} as unknown as Parameters<typeof layer.render>[1];
    expect(() => layer.render(stubGl, stubOptions)).not.toThrow();
  });
});
