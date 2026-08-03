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
            {
              type: "Feature",
              geometry: {
                type: "Polygon",
                coordinates: [[[116.3, 39.8], [116.5, 39.8], [116.5, 40.0], [116.3, 40.0], [116.3, 39.8]]],
              },
              properties: { name: "Area 1" },
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
      {
        id: "polys-layer",
        type: "fill",
        source: "s1",
        paint: {
          "fill-color": "#60a5fa",
          "fill-outline-color": "#1d4ed8",
        },
      },
    ],
  };

  it("compiles MapSpec to valid SVG string", () => {
    const svg = compileMapSpecToSvg(sampleMapSpec, { targetDpi: 72 });
    expect(svg).toContain("<svg");
    expect(svg).toContain("<circle");
    expect(svg).toContain("<path");
    expect(svg).toContain("<polygon");
    expect(svg).toContain("#de2d26");
    expect(svg).toContain("#2563eb");
  });

  it("scales stroke-width and radius proportionately according to targetDpi / 72 factor", () => {
    // at 300 DPI, dpiScale = 300 / 72 = 4.16666...
    // radius 6 * 4.16666 = 25
    // line-width 2 * 4.16666 = 8.33
    // polygon stroke-width 1 * 4.16666 = 4.17
    const svg = compileMapSpecToSvg(sampleMapSpec, { targetDpi: 300 });
    expect(svg).toContain('r="25"');
    expect(svg).toContain('stroke-width="8.33"');
    expect(svg).toContain('stroke-width="4.17"');
  });

  it("escapes paint color/opacity values to prevent SVG attribute injection (XSS)", () => {
    // A malicious MapSpec color with a double-quote would break out of the
    // attribute and inject arbitrary markup. The compiler must escape it.
    const malicious = {
      sources: {
        s1: {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                geometry: { type: "Point", coordinates: [116.4, 39.9] },
                properties: {},
              },
            ],
          },
        },
      },
      layers: [
        {
          id: "pts",
          type: "circle",
          source: "s1",
          // Tries to close the fill=" attribute and inject an onclick handler.
          paint: { "circle-color": 'red" onclick="alert(1)', "circle-opacity": 1 },
        },
      ],
    };
    const svg = compileMapSpecToSvg(malicious, { targetDpi: 72 });
    // The raw double-quote must NOT appear inside the attribute value.
    // The compiler escapes " -> &quot; so the injected onclick never becomes
    // a real attribute on the circle element.
    expect(svg).not.toContain('red" onclick');
    expect(svg).toContain("&quot;");
  });
});
