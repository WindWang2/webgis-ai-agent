import { describe, it, expect } from "vitest";
import { compileMapSpecToSvg, resolvePaintValue } from "./mapspec-to-svg";

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
    expect(svg).toContain('fill-rule="evenodd"');
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

  it("[MAPSPEC-01] resolves StyleMethod expressions (constant, field, match, step, interpolate)", () => {
    const props = { val: 15, cat: "B", score: 50 };

    expect(resolvePaintValue({ method: "constant", value: "#ff0000" })).toBe("#ff0000");
    expect(resolvePaintValue({ method: "field", field: "cat" }, props)).toBe("B");

    const matchSpec = { method: "match", field: "cat", cases: [["A", "#f00"], ["B", "#0f0"]], default: "#00f" };
    expect(resolvePaintValue(matchSpec, props)).toBe("#0f0");

    const stepSpec = { method: "step", field: "val", stops: [[10, "#f00"], [20, "#00f"]], default: "#fff" };
    expect(resolvePaintValue(stepSpec, { val: 5 })).toBe("#fff");
    expect(resolvePaintValue(stepSpec, { val: 15 })).toBe("#f00");
    expect(resolvePaintValue(stepSpec, { val: 25 })).toBe("#00f");

    const interpNum = { method: "interpolate", field: "score", stops: [[0, 10], [100, 50]] };
    expect(resolvePaintValue(interpNum, props)).toBe(30);

    const interpColor = { method: "interpolate", field: "score", stops: [[0, "#000000"], [100, "#ffffff"]] };
    expect(resolvePaintValue(interpColor, props)).toBe("#808080");
  });

  it("[MAPSPEC-02] renders multi-ring polygon with fill-rule='evenodd' to preserve holes", () => {
    const mapspecHole = {
      sources: {
        s1: {
          type: "geojson",
          data: {
            type: "Feature",
            geometry: {
              type: "Polygon",
              coordinates: [
                [[116.0, 39.0], [117.0, 39.0], [117.0, 40.0], [116.0, 40.0], [116.0, 39.0]],
                [[116.3, 39.3], [116.7, 39.3], [116.7, 39.7], [116.3, 39.7], [116.3, 39.3]],
              ],
            },
          },
        },
      },
      layers: [{ id: "p", type: "fill", source: "s1", paint: { "fill-color": "#123456" } }],
    };
    const svg = compileMapSpecToSvg(mapspecHole, { targetDpi: 72 });
    expect(svg).toContain('<path d="M ');
    expect(svg).toContain(" Z M ");
    expect(svg).toContain('fill-rule="evenodd"');
  });

  it("[MAPSPEC-03] balances ViewBox DPI scaling so viewBox scales proportionally with DPI", () => {
    const svg = compileMapSpecToSvg(sampleMapSpec, { targetDpi: 300, width: 1200, height: 800 });
    expect(svg).toContain('viewBox="0 0 5000 3333.33"');
  });

  it("[MAPSPEC-04] filters NaN coordinates in extractCoords and falls back to default extents", () => {
    const mapspecNaN = {
      sources: {
        s1: {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                geometry: { type: "Point", coordinates: [NaN, Infinity] },
              },
            ],
          },
        },
      },
      layers: [{ id: "l1", type: "circle", source: "s1" }],
    };
    const svg = compileMapSpecToSvg(mapspecNaN, { targetDpi: 72 });
    expect(svg).toContain("<svg");
    expect(svg).not.toContain("NaN");
  });

  it("[MAPSPEC-05] supports circle-stroke-color, circle-stroke-width, line-dasharray, line-linecap, line-linejoin", () => {
    const mapspecStroke = {
      sources: {
        s1: {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                geometry: { type: "Point", coordinates: [116.4, 39.9] },
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
              },
            ],
          },
        },
      },
      layers: [
        {
          id: "circle-stroke",
          type: "circle",
          source: "s1",
          paint: {
            "circle-color": "#3b82f6",
            "circle-stroke-color": "#000000",
            "circle-stroke-width": 2,
          },
        },
        {
          id: "line-styled",
          type: "line",
          source: "s1",
          layout: {
            "line-linecap": "round",
            "line-linejoin": "bevel",
          },
          paint: {
            "line-color": "#2563eb",
            "line-width": 2,
            "line-dasharray": [2, 4],
          },
        },
      ],
    };
    const svg = compileMapSpecToSvg(mapspecStroke, { targetDpi: 300 });
    expect(svg).toContain('stroke="#000000"');
    expect(svg).toContain('stroke-width="8.33"');
    expect(svg).toContain('stroke-linecap="round"');
    expect(svg).toContain('stroke-linejoin="bevel"');
    expect(svg).toContain('stroke-dasharray="8.33,16.67"');
  });

  it("[MAPSPEC-06] renders text halo and maps MapLibre text-anchor and dominant-baseline values", () => {
    const mapspecHalo = {
      sources: {
        s1: {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                geometry: { type: "Point", coordinates: [116.4, 39.9] },
                properties: { label: "Test Label" },
              },
            ],
          },
        },
      },
      layers: [
        {
          id: "label-halo",
          type: "symbol",
          source: "s1",
          layout: {
            "text-field": "{label}",
            "text-anchor": "top-left",
          },
          paint: {
            "text-color": "#000000",
            "text-halo-color": "#ffffff",
            "text-halo-width": 2,
          },
        },
      ],
    };
    const svg = compileMapSpecToSvg(mapspecHalo, { targetDpi: 300 });
    expect(svg).toContain('fill="none" stroke="#ffffff" stroke-width="16.67"');
    expect(svg).toContain('text-anchor="start"');
    expect(svg).toContain('dominant-baseline="hanging"');
    expect(svg).toContain("Test Label");
  });

  it("[MAPSPEC-07] computes bounding box centroid for polygon text label placement", () => {
    const mapspecPolyLabel = {
      sources: {
        s1: {
          type: "geojson",
          data: {
            type: "Feature",
            geometry: {
              type: "Polygon",
              coordinates: [
                [
                  [10, 20],
                  [30, 20],
                  [30, 40],
                  [10, 40],
                  [10, 20],
                ],
              ],
            },
            properties: { name: "PolyCenter" },
          },
        },
      },
      layers: [
        {
          id: "poly-text",
          type: "symbol",
          source: "s1",
          layout: { "text-field": "{name}" },
        },
      ],
    };
    const svg = compileMapSpecToSvg(mapspecPolyLabel, { targetDpi: 72 });
    // Bbox of polygon: minX=10, maxX=30, minY=20, maxY=40 -> Centroid is (20, 30)
    // Projecting [20, 30] in default view [10, 20, 30, 40] with width 1200 padding 40:
    // x = 40 + (20 - 10)/(30-10) * 1120 = 40 + 0.5 * 1120 = 600
    expect(svg).toContain('<text x="600"');
    expect(svg).toContain("PolyCenter");
  });

  it("[MAPSPEC-08] renders fallback for heatmap and fill-extrusion layer types", () => {
    const mapspecFallbacks = {
      sources: {
        s1: {
          type: "geojson",
          data: {
            type: "FeatureCollection",
            features: [
              {
                type: "Feature",
                geometry: { type: "Point", coordinates: [116.4, 39.9] },
              },
              {
                type: "Feature",
                geometry: {
                  type: "Polygon",
                  coordinates: [
                    [
                      [116.3, 39.8],
                      [116.5, 39.8],
                      [116.5, 40.0],
                      [116.3, 40.0],
                      [116.3, 39.8],
                    ],
                  ],
                },
              },
            ],
          },
        },
      },
      layers: [
        {
          id: "heat",
          type: "heatmap",
          source: "s1",
          paint: {
            "heatmap-color": "#ff0000",
            "heatmap-radius": 10,
          },
        },
        {
          id: "3d-bldg",
          type: "fill-extrusion",
          source: "s1",
          paint: {
            "fill-extrusion-color": "#334155",
            "fill-extrusion-opacity": 0.9,
          },
        },
      ],
    };
    const svg = compileMapSpecToSvg(mapspecFallbacks, { targetDpi: 72 });
    expect(svg).toContain('<circle');
    expect(svg).toContain('fill="#ff0000"');
    expect(svg).toContain('r="10"');
    expect(svg).toContain('<path d="M ');
    expect(svg).toContain('fill="#334155"');
    expect(svg).toContain('fill-opacity="0.9"');
  });
});
