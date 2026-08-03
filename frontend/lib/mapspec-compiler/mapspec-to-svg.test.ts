import { describe, it, expect } from "vitest";
import { compileMapSpecToSvg } from "./mapspec-to-svg";

describe("MapSpec-to-SVG Compiler Target", () => {
  const sampleMapSpec = {
    sources: {
      s1: {
        type: "geojson",
        data: {
          type: "FeatureCollection",
          features: [
            {
              type: "Feature",
              geometry: { type: "Point", coordinates: [116.4, 39.9] },
              properties: { name: "Beijing" },
            },
            {
              type: "Feature",
              geometry: {
                type: "LineString",
                coordinates: [
                  [116.4, 39.9],
                  [116.5, 40.0],
                ],
              },
              properties: { name: "Road 1" },
            },
          ],
        },
      },
    },
    layers: [
      {
        id: "points-layer",
        type: "circle",
        source: "s1",
        paint: {
          "circle-color": "#de2d26",
          "circle-radius": 6,
        },
      },
      {
        id: "lines-layer",
        type: "line",
        source: "s1",
        paint: {
          "line-color": "#2563eb",
          "line-width": 2,
        },
      },
    ],
  };

  it("compiles MapSpec to valid SVG string", () => {
    const svg = compileMapSpecToSvg(sampleMapSpec, { targetDpi: 72 });
    expect(svg).toContain("<svg");
    expect(svg).toContain("<circle");
    expect(svg).toContain("<path");
    expect(svg).toContain("#de2d26");
    expect(svg).toContain("#2563eb");
  });

  it("scales stroke-width and radius proportionately according to targetDpi / 72 factor", () => {
    // at 300 DPI, dpiScale = 300 / 72 = 4.16666...
    // radius 6 * 4.16666 = 25
    // line-width 2 * 4.16666 = 8.3333...
    const svg = compileMapSpecToSvg(sampleMapSpec, { targetDpi: 300 });
    expect(svg).toContain('r="25"');
    expect(svg).toContain('stroke-width="8.33"');
  });
});
