import { describe, it, expect } from "vitest";
import {
  renderSvgNorthArrow,
  renderSvgScalebar,
  renderSvgLegend,
  renderSvgPrintLayout,
} from "./svg-marginalia";

describe("SVG Marginalia Vector Renderer", () => {
  it("renders vector north arrow with N typography", () => {
    const svg = renderSvgNorthArrow({ width: 40, height: 40, color: "#2563eb" });
    expect(svg).toContain("<svg");
    expect(svg).toContain("polygon");
    expect(svg).toContain(">N<");
  });

  it("renders vector scalebar with distance label", () => {
    const svg = renderSvgScalebar({ lengthPx: 100, labelText: "5 km", color: "#1e293b" });
    expect(svg).toContain("<svg");
    expect(svg).toContain("line");
    expect(svg).toContain("5 km");
  });

  it("renders vector legend with layer items", () => {
    const svg = renderSvgLegend({
      title: "图例 Legend",
      items: [
        { label: "地震点", color: "#de2d26", type: "circle" },
        { label: "行政边界", color: "#2563eb", type: "line" },
      ],
    });
    expect(svg).toContain("<svg");
    expect(svg).toContain("图例 Legend");
    expect(svg).toContain("地震点");
    expect(svg).toContain("行政边界");
    expect(svg).toContain("#de2d26");
  });

  it("renders complete SVG print layout container", () => {
    const svg = renderSvgPrintLayout({
      layoutId: "tmpl_ly_academic",
      width: 1200,
      height: 800,
      title: "北京市地震分布专题图",
      scaleLabel: "10 km",
    });
    expect(svg).toContain("<svg");
    expect(svg).toContain("北京市地震分布专题图");
    expect(svg).toContain("10 km");
    expect(svg).toContain(">N<");
  });
});
