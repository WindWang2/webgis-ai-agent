/**
 * H02 — SVG 矢量导出 ref 层披露契约（任务书 §D：严禁 silent omit）。
 *
 * 历史：客户端 SVG 链不调 injectResolvedRefSources，非 HUD 挂靠的
 * ref 源层在孪生里静默丢（mapspec-to-svg.ts `if (!srcData) return`）。
 * 契约：未水合层必须产生 layer_ref_unavailable 结构化披露。
 */
import { describe, expect, it } from "vitest";

import { buildVectorSvgExport } from "@/lib/map-kit/vector-svg-export";
import { buildPublicationLayout } from "@/lib/export/layout-description";
import type { MapSpec } from "@/lib/mapspec-compiler/types.generated";

function specWithRefLayer(hydrated: boolean): MapSpec {
  const source: Record<string, unknown> = {
    type: "geojson",
    ref: "ref:geojson:xyz",
  };
  if (hydrated) {
    source.inlineData = {
      type: "FeatureCollection",
      features: [
        {
          type: "Feature",
          geometry: { type: "Point", coordinates: [104.06, 30.66] },
          properties: {},
        },
      ],
    };
  }
  return {
    version: "1.5",
    view: { center: [104.06, 30.66], zoom: 11 },
    layers: [
      {
        id: "l-ref",
        type: "circle",
        source: "src-ref",
        paint: { "circle-color": "#3388aa", "circle-radius": 6 },
      },
    ],
    sources: { "src-ref": source },
  } as unknown as MapSpec;
}

/** IR 装配 helper —— 与 exporter/vector-svg-export.test 同一构建器。 */
function layoutFor() {
  return buildPublicationLayout({
    paperSize: "screen",
    orientation: "landscape",
    dpi: 96,
    frame: { width: 1200, height: 800 },
    chromeModel: null,
  });
}

describe("H02 SVG ref layer disclosure", () => {
  it("unhydrated ref layer produces layer_ref_unavailable degradation", () => {
    const out = buildVectorSvgExport({
      spec: specWithRefLayer(false),
      layout: layoutFor(),
      dpi: 72,
    });
    const codes = out.degradations.map((d) => d.code);
    expect(codes).toContain("layer_ref_unavailable");
    expect(
      out.degradations.find((d) => d.code === "layer_ref_unavailable")?.detail,
    ).toContain("l-ref");
  });

  it("hydrated layer renders without ref degradation", () => {
    const out = buildVectorSvgExport({
      spec: specWithRefLayer(true),
      layout: layoutFor(),
      dpi: 72,
    });
    expect(out.degradations.map((d) => d.code)).not.toContain(
      "layer_ref_unavailable",
    );
    expect(out.svg).toContain("<circle");
  });
});
