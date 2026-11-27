/**
 * G20：派生数值栅格源的会话生命周期（setSessionScope → syncNumericRasterSources）。
 *
 * House style：hand-rolled stub map（同 runtime.test.ts），不 vi.mock maplibre。
 * 派生源 id = `${source}::${layer}`；瓦片 URL 内嵌 session_id —— 切会话必须
 * 让已挂载的派生源改挂新 URL，否则上一会话的瓦片继续上屏（数据串话）。
 */
import { describe, it, expect } from "vitest";
import { MapSpecRuntime } from "./runtime";
import type { MapSpec } from "@/lib/mapspec-compiler/types";

const rasterStyle = (styleId: string) => ({
  schema_version: "raster_expression.v1",
  mode: "single",
  bands: [1],
  stretch: {
    min: 0, max: 500, source: "manual", method: "manual",
    approximate: false, sample_count: null, per_channel: null,
  },
  colormap: "viridis",
  opacity: 0.7,
  resampling: "bilinear",
  classification: "continuous",
  data_revision: "fp:abc",
  style_id: styleId,
});

function numericSpec(styleId = "style-aaaa"): MapSpec {
  return {
    version: "1.9",
    sources: {
      shared: {
        type: "numeric_raster",
        schema_version: "raster_expression.v1",
        refId: "ref:raster/four-band",
        bounds: [10, 19, 11, 20],
        crs: "EPSG:4326",
        dataRevision: "fp:abc",
        bandCount: 4,
      },
    },
    layers: [
      { id: "layer-a", source: "shared", type: "raster", paint: {}, raster_style: rasterStyle(styleId) },
    ],
  } as unknown as MapSpec;
}

function makeMockMap() {
  const sources: Record<string, any> = {};
  const layers: any[] = [];
  const calls = {
    addSource: [] as string[],
    removeSource: [] as string[],
    addLayer: [] as string[],
    removeLayer: [] as string[],
  };
  const setTilesCalls: Array<{ id: string; tiles: string[] }> = [];
  const map: any = {
    isStyleLoaded: () => true,
    getStyle: () => ({ sources, layers }),
    getSource(id: string) { return sources[id]; },
    getLayer(id: string) { return layers.find((l) => l.id === id); },
    addSource(id: string, def: any) {
      // MapLibre RasterTileSource 语义：瓦片 URL 可原地换（setTiles → load(force)）。
      sources[id] = {
        ...def,
        setTiles: (tiles: string[]) => {
          sources[id].tiles = tiles;
          setTilesCalls.push({ id, tiles });
        },
      };
      calls.addSource.push(id);
    },
    removeSource(id: string) { delete sources[id]; calls.removeSource.push(id); },
    addLayer(def: any) {
      if (layers.some((l) => l.id === def.id)) {
        throw new Error(`Layer with id ${def.id} already exists on this map`);
      }
      layers.push(def);
      calls.addLayer.push(def.id);
    },
    removeLayer(id: string) {
      const i = layers.findIndex((l) => l.id === id);
      if (i >= 0) layers.splice(i, 1);
      calls.removeLayer.push(id);
    },
    on() { /* 无事件面 */ },
    off() { /* 无事件面 */ },
    _sources: sources,
    _layers: layers,
    _calls: calls,
    _setTilesCalls: setTilesCalls,
  };
  return map;
}

const tilesOf = (map: any, id: string): string[] => (map.getSource(id)?.tiles ?? []) as string[];

describe("G20 numeric raster derived sources — session lifetime", () => {
  it("切会话后派生源改挂新会话瓦片 URL（旧会话瓦片不得继续上屏）", () => {
    const map = makeMockMap();
    const rt = new MapSpecRuntime(map as never);
    rt.setSessionScope("sess-a");
    rt.reconcile(numericSpec());
    expect(tilesOf(map, "shared::layer-a")[0]).toContain("session_id=sess-a");

    rt.setSessionScope("sess-b");
    // 同一内容的新 spec 对象、无 setStyle —— 只有瓦片 URL 该变。
    rt.reconcile(numericSpec());
    expect(tilesOf(map, "shared::layer-a")[0]).toContain("session_id=sess-b");
    expect(tilesOf(map, "shared::layer-a")[0]).not.toContain("session_id=sess-a");
    // 换 URL 不重建图层（层仍在图上、未被卸载）。
    expect(map.getLayer("layer-a")).toBeTruthy();
    expect(map._calls.removeLayer).not.toContain("layer-a");
    rt.dispose();
  });

  it("style_id 变更同样把新 URL 挂上已存在的派生源", () => {
    const map = makeMockMap();
    const rt = new MapSpecRuntime(map as never);
    rt.setSessionScope("sess-a");
    rt.reconcile(numericSpec("style-aaaa"));
    expect(tilesOf(map, "shared::layer-a")[0]).toContain("style_id=style-aaaa");

    rt.reconcile(numericSpec("style-bbbb"));
    expect(tilesOf(map, "shared::layer-a")[0]).toContain("style_id=style-bbbb");
    expect(map.getLayer("layer-a")).toBeTruthy();
    rt.dispose();
  });

  it("内容无变化的 reconcile 不重复重载瓦片", () => {
    const map = makeMockMap();
    const rt = new MapSpecRuntime(map as never);
    rt.setSessionScope("sess-a");
    rt.reconcile(numericSpec());
    rt.setSessionScope("sess-b");
    rt.reconcile(numericSpec());
    const reloads = map._setTilesCalls.length;
    expect(reloads).toBe(1);
    rt.reconcile(numericSpec());
    rt.reconcile(numericSpec());
    expect(map._setTilesCalls.length).toBe(reloads);
    rt.dispose();
  });

  it("spec 去掉数值栅格层 → 派生源随之卸载（不留跨会话残留）", () => {
    const map = makeMockMap();
    const rt = new MapSpecRuntime(map as never);
    rt.setSessionScope("sess-a");
    rt.reconcile(numericSpec());
    expect(map.getSource("shared::layer-a")).toBeTruthy();

    rt.setSessionScope("sess-b");
    const noLayers = numericSpec();
    (noLayers as { layers: unknown[] }).layers = [];
    rt.reconcile(noLayers);
    expect(map.getSource("shared::layer-a")).toBeFalsy();
    expect(map._calls.removeSource).toContain("shared::layer-a");
    expect(map.getLayer("layer-a")).toBeFalsy();
    rt.dispose();
  });
});
