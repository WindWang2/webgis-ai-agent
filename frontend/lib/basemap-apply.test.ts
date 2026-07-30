import { describe, it, expect } from "vitest";
import { applyBaseline, BasemapPayload } from "./basemap-apply";
import { TILE_PROVIDERS } from "./providers";

describe("applyBaseline reducer", () => {
  it("vector branch: vectorStyleUrl presence produces vector style spec", () => {
    const payload: BasemapPayload = {
      providerId: "carto-positron",
      vectorStyleUrl: "https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
    };

    const style = applyBaseline(payload);

    expect(style.vectorStyleUrl).toBe("https://basemaps.cartocdn.com/gl/positron-gl-style/style.json");
    expect(style.version).toBe(8);
  });

  it("raster branch: injects rasterFilters paint properties into raster layer", () => {
    const payload: BasemapPayload = {
      providerId: "osm",
      rasterFilters: {
        saturation: -1,
        contrast: 0.2,
        brightness: 1.1,
        opacity: 0.9,
      },
    };

    const style = applyBaseline(payload);

    expect(style.version).toBe(8);
    expect(style.sources["raster-osm"]).toBeDefined();
    expect(style.layers.length).toBeGreaterThanOrEqual(1);

    const layer = style.layers.find((l: any) => l.id === "raster-layer-osm");
    expect(layer).toBeDefined();
    expect(layer?.paint["raster-saturation"]).toBe(-1);
    expect(layer?.paint["raster-contrast"]).toBe(0.2);
    expect(layer?.paint["raster-opacity"]).toBe(0.9);
  });

  it("overlay branch: stacks imagery base + vector labels overlay layers", () => {
    const payload: BasemapPayload = {
      providerId: "esri-img",
      overlays: [
        {
          providerId: "carto-positron",
          vectorStyleUrl: "https://basemaps.cartocdn.com/gl/positron-nolabels-gl-style/style.json",
          opacity: 0.8,
        },
      ],
    };

    const style = applyBaseline(payload);

    expect(style.version).toBe(8);
    expect(style.layers.length).toBeGreaterThanOrEqual(2);
    const overlayLayer = style.layers.find((l: any) => l.id.includes("overlay"));
    expect(overlayLayer).toBeDefined();
  });
});
