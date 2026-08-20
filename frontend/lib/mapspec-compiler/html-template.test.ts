import { describe, it, expect } from "vitest";
import { generateMapHtml } from "./html-template";

describe("html-template __ORIGIN__ placeholder (#697)", () => {
  it("replaces __ORIGIN__ with location.origin before creating the map", () => {
    const style = {
      version: 8,
      sources: {
        pts: {
          type: "vector",
          tiles: ["__ORIGIN__/tiles/{z}/{x}/{y}.mvt"],
          minzoom: 0,
          maxzoom: 2,
        },
      },
      layers: [{ id: "l", type: "circle", source: "pts", "source-layer": "data" }],
      center: [0, 0] as [number, number],
      zoom: 2,
    };
    const html = generateMapHtml(style);
    // Must contain the replacement logic
    expect(html).toContain('replaceAll("__ORIGIN__"');
    expect(html).toContain("location.origin");
    // Must resolve before new maplibregl.Map and use resolvedStyle
    expect(html).toContain("__resolvedStyle");
    expect(html).toContain("new maplibregl.Map");
    // The resolved style is used as the map's style (not the raw window var)
    expect(html).toContain("style: __resolvedStyle");
    // Mechanics unchanged: __MAP_LOADED__ / __MAP_IDLE__ and window.__MAP__ still present
    expect(html).toContain("window.__MAP_LOADED__");
    expect(html).toContain("window.__MAP_IDLE__");
    expect(html).toContain("window.__MAP__ = map");
    // The literal __ORIGIN__ should appear only inside the style JSON + the replace call,
    // not as a hard-coded URL (i.e. html must not contain localhost/127.0.0.1 ports)
    expect(html).not.toContain("127.0.0.1");
    expect(html).not.toContain("localhost");
  });

  it("keeps inlined style JSON containing the placeholder verbatim", () => {
    const style = {
      version: 8,
      sources: { v: { type: "vector", tiles: ["__ORIGIN__/tiles/{z}/{x}/{y}.mvt"] } },
      layers: [],
    };
    const html = generateMapHtml(style as any);
    // The initial assignment keeps __ORIGIN__ literal — replacement happens at runtime
    expect(html).toContain("__ORIGIN__/tiles/{z}/{x}/{y}.mvt");
  });
});
