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
    _mapspecProjectionFingerprint: "runtime-sha256:projection",
    _mapspecRepairActionId: "ma-carto-1",
    _intentGeneration: 7,
  };
  return { map, spec, hud };
}

describe("cartographic runtime evidence", () => {
  it("proves exact live source, style and visibility without source data", () => {
    const { map, spec, hud } = fixture();
    hud.opacity = 0.1; // stale HUD projection must not become runtime evidence
    const observation = collectCartographicRuntimeObservation(
      map, spec, [hud], "carto-sha256:abc", "", spec,
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
      runtime_store_id: "ref:geojson-1",
      projection_fingerprint: "runtime-sha256:projection",
      repair_action_id: "ma-carto-1",
      intent_generation: 7,
    })]);
    expect(JSON.stringify(observation)).not.toContain("features");
  });

  it("reports a missing/rejected MapLibre layer instead of fake convergence", () => {
    const { map, spec, hud } = fixture();
    map.getLayer = () => undefined;

    const observation = collectCartographicRuntimeObservation(
      map, spec, [hud], "carto-sha256:abc", "add_layer_failed", spec,
    ) as any;

    expect(observation.layers[0].style_converged).toBe(false);
    expect(observation.layers[0].runtime_layer_count).toBe(0);
    expect(observation.reconcile_error).toBe("add_layer_failed");
  });

  it("rejects wrong live layer type and user-invalidated generation attestation", () => {
    const { map, spec, hud } = fixture();
    const originalGetLayer = map.getLayer;
    map.getLayer = (id: string) => {
      const layer = originalGetLayer(id);
      return layer ? { ...layer, type: "fill" } : undefined;
    };
    hud._mapspecFingerprint = undefined;

    const observation = collectCartographicRuntimeObservation(
      map, spec, [hud], "carto-sha256:abc", "", spec,
    ) as any;

    expect(observation.layers[0].generation_attested).toBe(false);
    expect(observation.layers[0].projection_fingerprint).toBeUndefined();
    expect(observation.layers[0].style_converged).toBe(false);
  });

  it("does not certify a same-id source from an older applied generation", () => {
    const { map, spec, hud } = fixture();
    const stale: MapSpec = {
      ...spec,
      sources: {
        "ref:geojson-1": {
          type: "geojson",
          inlineData: { type: "FeatureCollection", features: [] },
        },
      },
    };

    const observation = collectCartographicRuntimeObservation(
      map, spec, [hud], "carto-sha256:abc", "", stale,
    ) as any;

    expect(observation.layers[0].source_converged).toBe(false);
    expect(observation.layers[0].style_converged).toBe(false);
  });

  it("maps a declarative raster image source to MapLibre's live image type", () => {
    const { map, hud } = fixture();
    hud._mapspecFingerprint = "carto-sha256:raster";
    const rasterLayer = {
      id: "ref:geojson-1__raster",
      source: "ref:geojson-1",
      type: "raster",
      paint: { "raster-opacity": 0.8 },
      layout: { visibility: "visible" },
    };
    const liveImage = { type: "image" };
    map.getLayer = (id: string) => id === rasterLayer.id ? rasterLayer : undefined;
    map.getSource = (id: string) => id === rasterLayer.source ? liveImage : undefined;
    map.getStyle = () => ({
      sources: { "ref:geojson-1": liveImage },
      layers: [rasterLayer],
    });
    map.getPaintProperty = (_id: string, key: string) => (rasterLayer.paint as any)[key];
    map.getLayoutProperty = (_id: string, key: string) => (rasterLayer.layout as any)[key];
    const spec: MapSpec = {
      version: "1.0",
      sources: {
        "ref:geojson-1": {
          type: "raster",
          imageRef: "ref:raster/result",
          bounds: [100, 20, 101, 21],
        },
      },
      layers: [rasterLayer as any],
    };

    const observation = collectCartographicRuntimeObservation(
      map, spec, [hud], "carto-sha256:raster", "", spec,
    ) as any;

    expect(observation.layers[0].source_converged).toBe(true);
    expect(observation.layers[0].style_converged).toBe(true);
  });
});

describe("cartographic runtime evidence — v2 FE4 family keys", () => {
  it("committed spec 平铺 id（无 __ 子层）也能匹配期望族", () => {
    const specLayer = {
      id: "product-abc",
      source: "src-1",
      type: "circle",
      paint: { "circle-color": "#3366cc" },
      layout: { visibility: "visible" },
    };
    const map: any = {
      isStyleLoaded: () => true,
      getLayer: (id: string) => id === specLayer.id ? specLayer : undefined,
      getSource: (id: string) => id === specLayer.source ? { type: "geojson" } : undefined,
      getPaintProperty: (id: string, key: string) => (
        id === specLayer.id ? (specLayer.paint as any)[key] : undefined
      ),
      getLayoutProperty: (id: string, key: string) => (
        id === specLayer.id ? (specLayer.layout as any)[key] : undefined
      ),
      getCenter: () => ({ lng: 100.5, lat: 20.5 }),
      getZoom: () => 8,
      getBearing: () => 0,
      getPitch: () => 0,
    };
    const spec: MapSpec = {
      version: "1.0",
      sources: { "src-1": { type: "geojson" } as any },
      layers: [specLayer as any],
    };
    // ref-mounted 行：hud.id 是 ref，spec 层 id 是 product-*（审计 FE4 场景）
    const hud: Layer = {
      id: "ref:geojson-9",
      name: "Result",
      type: "vector",
      visible: true,
      opacity: 0.9,
      _refId: "ref:geojson-9",
      _mapspecLayerId: "product-abc",
      _mapspecFingerprint: "fp-1",
    };
    const observation = collectCartographicRuntimeObservation(
      map, spec, [hud], "fp-1", "", spec,
    ) as any;
    const row = observation.layers[0];
    expect(row.visible).toBe(true);
    expect(row.runtime_layer_count).toBe(1);
    expect(row.source_converged).toBe(true);
  });
});
