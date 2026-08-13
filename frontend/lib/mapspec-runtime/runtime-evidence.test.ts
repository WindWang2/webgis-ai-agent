import { describe, expect, it } from "vitest";
import { collectCartographicRuntimeObservation } from "./runtime-evidence";
import type { MapSpec } from "@/lib/mapspec-compiler/types";
import type { Layer } from "@/lib/types/layer";

function fixture() {
  const runtimeLayer = {
    id: "ref:geojson-1__point",
    source: "ref:geojson-1",
    type: "circle",
    paint: { "circle-color": "#3366cc", "circle-opacity": 0.8 },
    layout: { visibility: "visible" },
  };
  const source = { type: "geojson" };
  const map: any = {
    isStyleLoaded: () => true,
    getLayer: (id: string) => id === runtimeLayer.id ? runtimeLayer : undefined,
    getSource: (id: string) => id === runtimeLayer.source ? source : undefined,
    getPaintProperty: (id: string, key: string) => (
      id === runtimeLayer.id ? (runtimeLayer.paint as any)[key] : undefined
    ),
    getLayoutProperty: (id: string, key: string) => (
      id === runtimeLayer.id ? (runtimeLayer.layout as any)[key] : undefined
    ),
    getCenter: () => ({ lng: 100.5, lat: 20.5 }),
    getZoom: () => 8,
    getBearing: () => 0,
    getPitch: () => 0,
  };
  const spec: MapSpec = {
    version: "1.0",
    sources: { "ref:geojson-1": source as any },
    layers: [runtimeLayer as any],
  };
  const hud: Layer = {
    id: "ref:geojson-1",
    name: "Result",
    type: "vector",
    visible: true,
    opacity: 0.8,
    source: { type: "FeatureCollection", features: [] },
    _refId: "ref:geojson-1",
    _mapspecLayerId: "result",
    _mapspecFingerprint: "carto-sha256:abc",
  };
  return { map, spec, hud };
}

describe("cartographic runtime evidence", () => {
  it("proves exact live source, style and visibility without source data", () => {
    const { map, spec, hud } = fixture();
    const observation = collectCartographicRuntimeObservation(
      map, spec, [hud], "carto-sha256:abc",
    ) as any;

    expect(observation.mapspec_fingerprint).toBe("carto-sha256:abc");
    expect(observation.layers).toEqual([expect.objectContaining({
      id: "result",
      _refId: "ref:geojson-1",
      visible: true,
      opacity: 0.8,
      source_converged: true,
      style_converged: true,
      runtime_layer_count: 1,
    })]);
    expect(JSON.stringify(observation)).not.toContain("features");
  });

  it("reports a missing/rejected MapLibre layer instead of fake convergence", () => {
    const { map, spec, hud } = fixture();
    map.getLayer = () => undefined;

    const observation = collectCartographicRuntimeObservation(
      map, spec, [hud], "carto-sha256:abc", "add_layer_failed",
    ) as any;

    expect(observation.layers[0].style_converged).toBe(false);
    expect(observation.layers[0].runtime_layer_count).toBe(0);
    expect(observation.reconcile_error).toBe("add_layer_failed");
  });
});
