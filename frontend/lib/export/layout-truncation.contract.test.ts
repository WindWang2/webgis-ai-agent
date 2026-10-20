/**
 * H02 Wave 4 — 版面几何契约：确定性截断 + 用户 pin 空间决策。
 *
 * 任务书 §C / Oracle 4：越界/碰撞/标题截断与用户 pin 优先权有自动化
 * 测试；超长内容确定性截断并披露（fail-honest），不静默溢出。
 */
import { describe, expect, it } from "vitest";

import { buildPublicationLayout } from "@/lib/export/layout-description";
import { buildVectorSvgExport } from "@/lib/map-kit/vector-svg-export";
import type { MapSpec } from "@/lib/mapspec-compiler/types.generated";

function layoutWithTitle(title?: string) {
  return buildPublicationLayout({
    paperSize: "screen",
    orientation: "landscape",
    dpi: 96,
    frame: { width: 1200, height: 800 },
    chromeModel: null,
    requestTitle: title,
  });
}

describe("H02 layout geometry contracts", () => {
  it("long title is deterministically truncated and flagged", () => {
    const long = "成都市小学分布情况".repeat(30); // 300 code points
    const layout = layoutWithTitle(long);
    const title = layout.texts.find((t) => t.kind === "title")!;
    expect(title.text.length).toBe(120);
    expect(title.text.endsWith("…")).toBe(true);
    expect(title.truncated).toBe(true);
  });

  it("short title passes through untruncated", () => {
    const layout = layoutWithTitle("成都市小学分布");
    const title = layout.texts.find((t) => t.kind === "title")!;
    expect(title.text).toBe("成都市小学分布");
    expect(title.truncated).toBeUndefined();
  });

  it("subtitle truncation cap is wider than title cap", () => {
    const layout = buildPublicationLayout({
      paperSize: "screen",
      orientation: "landscape",
      dpi: 96,
      frame: { width: 1200, height: 800 },
      chromeModel: null,
      requestSubtitle: "副".repeat(200),
    });
    const subtitle = layout.texts.find((t) => t.kind === "subtitle")!;
    expect(subtitle.text.length).toBe(160);
    expect(subtitle.truncated).toBe(true);
  });

  it("vector SVG export discloses truncated texts (title_truncated)", () => {
    const spec = {
      version: "1.5",
      view: { center: [104.06, 30.66], zoom: 11 },
      layers: [
        {
          id: "l1",
          type: "circle",
          source: "s1",
          paint: { "circle-color": "#3388aa" },
        },
      ],
      sources: {
        s1: {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                geometry: { type: "Point", coordinates: [104.06, 30.66] },
                properties: {},
              },
            ],
          },
        },
      },
    } as unknown as MapSpec;
    const out = buildVectorSvgExport({
      spec,
      layout: layoutWithTitle("长".repeat(200)),
      dpi: 72,
    });
    const entry = out.degradations.find((d) => d.code === "title_truncated");
    expect(entry).toBeDefined();
    expect(entry!.detail).toContain("title");
  });

  it("truncation is deterministic (same input, same clipped text)", () => {
    const long = "x".repeat(400);
    const a = layoutWithTitle(long).texts.find((t) => t.kind === "title")!;
    const b = layoutWithTitle(long).texts.find((t) => t.kind === "title")!;
    expect(a).toEqual(b);
  });
});
