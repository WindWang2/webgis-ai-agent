/**
 * Compiler parity contract test (TypeScript side).
 *
 * Locks the TS MapSpec-to-SVG compiler against its Python twin so the two
 * duplicated implementations do not silently drift (review Standards finding #1).
 * The same fixture file is consumed by `tests/unit/test_compiler_parity.py`;
 * both sides assert the same normalized invariants (element counts, DPI-scaled
 * dimensions, default colors, attribute shapes). If you change the compiler
 * output, update both tests together.
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
    expect(count(svg, "path")).toBe(1);
    expect(count(svg, "polygon")).toBe(1);
  });

  it("scales by the same DPI factor (targetDpi / 72) as the Python twin", () => {
    const svg = compileMapSpecToSvg(mapspec, { targetDpi: 300 });
    // radius 5 * (300/72) = 20.83; line-width 2 * (300/72) = 8.33;
    // outline 1.0 * (300/72) = 4.17
    expect(svg).toContain('r="20.83"');
    expect(svg).toContain('stroke-width="8.33"');
    expect(svg).toContain('stroke-width="4.17"');
  });

  it("emits the same default colors and group wrapper as the Python twin", () => {
    const svg = compileMapSpecToSvg(mapspec, { targetDpi: 72 });
    expect(svg).toContain('<g class="mapspec-vector-layers">');
    expect(svg).toContain("#de2d26"); // circle-color
    expect(svg).toContain("#2563eb"); // line-color
    expect(svg).toContain("#60a5fa"); // fill-color
    expect(svg).toContain("#1d4ed8"); // fill-outline-color
    expect(svg).toContain('fill-opacity="0.6"');
  });

  it("wraps in the same svg root + white background rect as the Python twin", () => {
    const svg = compileMapSpecToSvg(mapspec, { targetDpi: 72, width: 800, height: 600 });
    expect(svg.startsWith('<svg width="800" height="600"')).toBe(true);
    expect(svg).toContain('viewBox="0 0 800 600"');
    expect(svg).toContain('<rect width="100%" height="100%" fill="#ffffff" />');
  });
});
