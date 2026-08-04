/**
 * Compiler parity contract test (TypeScript side).
 *
 * Locks the TS MapSpec-to-SVG compiler against its Python twin so the two
 * duplicated implementations do not silently drift (review Standards finding #1).
 * The same fixture file is consumed by `tests/unit/test_compiler_parity.py`;
 * both sides assert the SAME normalized invariants (element counts, DPI-scaled
 * dimensions, default colors, attribute shapes, and now coordinates). If you
 * change the compiler output, update both tests together.
 *
 * P0-2 fix: assertions previously used an `or` mask (`'font-size="50.0"' in svg
 * or 'font-size="50"' in svg`) that accepted BOTH forms, so twin drift could
 * never fail. Now both twins emit one canonical minimal form (fmtNum strips
 * trailing zeros: `50` not `50.0`, `1` not `1.0`), and the tests pin exactly
 * that canonical string.
 */
import { describe, it, expect } from "vitest";
import { compileMapSpecToSvg } from "./mapspec-to-svg";
import mapspec from "../../../tests/fixtures/compiler_parity_mapspec.json";

function count(svg: string, tag: string): number {
  const re = new RegExp(`<${tag}\\b`, "g");
  return (svg.match(re) || []).length;
}

describe("MapSpec-to-SVG compiler parity (TS twin)", () => {
  it("compiles fixture to the same element counts as the Python twin", () => {
    const svg = compileMapSpecToSvg(mapspec, { targetDpi: 72 });
    expect(count(svg, "circle")).toBe(1);
    expect(count(svg, "path")).toBe(2);
    expect(count(svg, "polygon")).toBe(0);
    expect(count(svg, "text")).toBe(3);
  });

  it("scales by the same DPI factor (targetDpi / 72) as the Python twin", () => {
    const svg = compileMapSpecToSvg(mapspec, { targetDpi: 300 });
    // radius 5 * (300/72) = 20.83; line-width 2 * (300/72) = 8.33;
    // outline 1.0 * (300/72) = 4.17; font-size 12 * (300/72) = 50.0.
    // Canonical minimal form (fmtNum): "20.83", "8.33", "4.17", "50".
    expect(svg).toContain('r="20.83"');
    expect(svg).toContain('stroke-width="8.33"');
    expect(svg).toContain('stroke-width="4.17"');
    // ONE canonical string - no `or` mask. Both twins emit font-size="50".
    expect(svg).toContain('font-size="50"');
    expect(svg).not.toContain('font-size="50.0"');
    expect(svg).toContain("Beijing");
  });

  it("emits the same default colors, opacities, and coordinates as the Python twin", () => {
    const svg = compileMapSpecToSvg(mapspec, { targetDpi: 72 });
    expect(svg).toContain('<g class="mapspec-vector-layers">');
    expect(svg).toContain("#de2d26"); // circle-color
    expect(svg).toContain("#2563eb"); // line-color
    expect(svg).toContain("#60a5fa"); // fill-color
    expect(svg).toContain("#1d4ed8"); // fill-outline-color
    // Default opacities are canonical minimal form: "1" not "1.0".
    expect(svg).toContain('fill-opacity="0.6"');
    expect(svg).toContain('fill-opacity="1"');
    expect(svg).not.toContain('fill-opacity="1.0"');
    // Coordinate parity: the range bug (Python clamped small ranges to 1.0)
    // is fixed; both twins now project the Point (116.4, 39.9) to cx="413.33".
    expect(svg).toContain('cx="413.33"');
    expect(svg).toContain('cy="400.26"');
  });

  it("wraps in the same svg root + white background rect as the Python twin", () => {
    const svg = compileMapSpecToSvg(mapspec, { targetDpi: 72, width: 800, height: 600 });
    expect(svg.startsWith('<svg width="800" height="600"')).toBe(true);
    expect(svg).toContain('viewBox="0 0 800 600"');
    expect(svg).toContain('<rect width="100%" height="100%" fill="#ffffff" />');
  });

  it("emits raster <image> with oversample boost matching the Python twin", () => {
    // Parity with test_python_compiles_raster_layer_with_oversample_boost:
    // both twins must emit the same <image> tag shape for raster layers.
    const rasterMapSpec = {
      sources: {
        r1: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"] },
      },
      layers: [
        { id: "r-base", type: "raster", source: "r1", paint: { "raster-opacity": 0.8 } },
      ],
    };
    const svg = compileMapSpecToSvg(rasterMapSpec, { targetDpi: 300 });
    expect(svg).toContain("<image");
    expect(svg).toContain('data-oversample-boost="2"');
    // Canonical minimal form: opacity "0.8" (not "0.80").
    expect(svg).toContain('opacity="0.8"');
  });

  it("emits tile grid pixel bounds matching the Python twin for bounded raster maps", () => {
    const rasterMapSpec = {
      sources: {
        v1: {
          type: "geojson",
          data: {
            type: "Feature",
            geometry: {
              type: "Polygon",
              coordinates: [[[116.3, 39.8], [116.6, 39.8], [116.6, 40.0], [116.3, 40.0], [116.3, 39.8]]],
            },
          },
        },
        r1: { type: "raster", tiles: ["https://tile.openstreetmap.org/{z}/{x}/{y}.png"] },
      },
      layers: [
        { id: "r-base", type: "raster", source: "r1", paint: { "raster-opacity": 0.8 } },
      ],
    };
    const svg = compileMapSpecToSvg(rasterMapSpec, { targetDpi: 300, width: 1200, height: 800, padding: 40 });
    const matches = Array.from(svg.matchAll(/<image\s+x="([^"]+)"\s+y="([^"]+)"\s+width="([^"]+)"\s+height="([^"]+)"/g));
    expect(matches.length).toBeGreaterThan(0);
    expect(matches[0].slice(1, 5)).toEqual(["-155.38", "-501.09", "1367.19", "1011.4"]);
  });
});
