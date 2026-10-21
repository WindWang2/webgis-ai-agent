import { describe, it, expect, beforeEach, vi } from "vitest";
import { MapSpecRuntime } from "./runtime";
import type { Map as MaplibreMap } from "maplibre-gl";
import type { MapSpec, MapSpecLayer } from "@/lib/mapspec-compiler/types";
import { getSymbolLawEvidence, resetSymbolLawEvidence } from "@/lib/map-kit/symbol-law";

/**
 * v1.6（方向 5）：MapSpecRuntime live 消费表达窗口的展开/清理/显隐契约。
 *
 * mock map 复用 runtime.test.ts 的手写桩纪律（不 vi.mock maplibre-gl），
 * 额外补 setLayoutProperty/setPaintProperty/setFilter/getZoom 面。
 */

/** 手写桩的 MapLibre style 层形状（测试断言面子集；其余字段不透明）。 */
interface MockLayer {
  id: string;
  type?: string;
  minzoom?: number;
  maxzoom?: number;
  filter?: unknown;
  layout?: Record<string, unknown>;
  paint?: Record<string, unknown>;
  [key: string]: unknown;
}

/** 手写桩的源形状（GeoJSON 源配置 + setData 桩）。 */
interface MockSource {
  setData?: ReturnType<typeof vi.fn>;
  [key: string]: unknown;
}

/** 桩记录的层/源定义（桩不解释其内部结构，按不透明对象记录）。 */
type LayerDef = Record<string, unknown> & { id: string };
type StyleDef = Record<string, unknown>;

/** 桩记录的 map 方法调用序列（断言面）。 */
interface MockCalls {
  addSource: Array<{ id: string; def: StyleDef }>;
  removeSource: string[];
  addLayer: Array<{ def: LayerDef }>;
  removeLayer: string[];
  moveLayer: string[];
  setLayoutProperty: Array<{ id: string; key: string; value: unknown }>;
  setPaintProperty: Array<{ id: string; key: string; value: unknown }>;
  setFilter: Array<{ id: string; filter: unknown }>;
}

/** 手写桩 map（ADR-0036 纪律：不 vi.mock maplibre-gl，记录调用序列）。 */
interface MockMap {
  isStyleLoaded(): boolean;
  getZoom(): number;
  getStyle(): { sources: Record<string, MockSource>; layers: MockLayer[] };
  getSource(id: string): MockSource | undefined;
  getLayer(id: string): MockLayer | undefined;
  addSource(id: string, def: StyleDef): void;
  removeSource(id: string): void;
  addLayer(def: LayerDef): void;
  removeLayer(id: string): void;
  moveLayer(id: string): void;
  setLayoutProperty(id: string, key: string, value: unknown): void;
  setPaintProperty(id: string, key: string, value: unknown): void;
  setFilter(id: string, filter: unknown): void;
  on(event: string, handler: () => void): void;
  off(event: string, handler: () => void): void;
  _emit(event: string): void;
  _sources: Record<string, MockSource>;
  _calls: MockCalls;
}

function makeMockMap(): MockMap {
  const sources: Record<string, MockSource> = {};
  const layers: MockLayer[] = [];
  const listeners = new Map<string, Set<() => void>>();
  const calls: MockCalls = {
    addSource: [],
    removeSource: [],
    addLayer: [],
    removeLayer: [],
    moveLayer: [],
    setLayoutProperty: [],
    setPaintProperty: [],
    setFilter: [],
  };
  const map: MockMap = {
    isStyleLoaded: () => true,
    getZoom: () => 10,
    getStyle: () => ({ sources, layers }),
    getSource(id: string) { return sources[id]; },
    getLayer(id: string) { return layers.find((l) => l.id === id); },
    addSource(id: string, def: StyleDef) {
      sources[id] = { ...def, setData: vi.fn() };
      calls.addSource.push({ id, def });
    },
    removeSource(id: string) {
      delete sources[id];
      calls.removeSource.push(id);
    },
    addLayer(def: LayerDef) {
      if (layers.some((l) => l.id === def.id)) {
        throw new Error(`Layer with id ${def.id} already exists on this map`);
      }
      layers.push(def);
      calls.addLayer.push({ def });
    },
    removeLayer(id: string) {
      const i = layers.findIndex((l) => l.id === id);
      if (i >= 0) layers.splice(i, 1);
      calls.removeLayer.push(id);
    },
    moveLayer(id: string) { calls.moveLayer.push(id); },
    setLayoutProperty(id: string, key: string, value: unknown) {
      const l = layers.find((x) => x.id === id);
      if (l) l.layout = { ...(l.layout ?? {}), [key]: value };
      calls.setLayoutProperty.push({ id, key, value });
    },
    setPaintProperty(id: string, key: string, value: unknown) {
      calls.setPaintProperty.push({ id, key, value });
    },
    setFilter(id: string, filter: unknown) {
      const l = layers.find((x) => x.id === id);
      if (l) l.filter = filter;
      calls.setFilter.push({ id, filter });
    },
    on(event: string, handler: () => void) {
      const handlers = listeners.get(event) ?? new Set();
      handlers.add(handler);
      listeners.set(event, handlers);
    },
    off(event: string, handler: () => void) {
      listeners.get(event)?.delete(handler);
    },
    _emit(event: string) {
      for (const handler of Array.from(listeners.get(event) ?? [])) handler();
    },
    _sources: sources,
    _calls: calls,
  };
  return map;
}

/** 桩 → MaplibreMap 桥（runtime.ts 顶部注记的推荐消费方式）。 */
function makeRuntime(map: MockMap): MapSpecRuntime {
  return new MapSpecRuntime(map as unknown as MaplibreMap);
}

function pointFeatures(n: number) {
  return {
    type: "FeatureCollection" as const,
    features: Array.from({ length: n }, (_, i) => ({
      type: "Feature" as const,
      geometry: { type: "Point" as const, coordinates: [116.4 + i * 0.01, 39.9] },
      properties: { v: i },
    })),
  };
}

const HEAT_CLUSTER_LAYER: MapSpecLayer = {
  id: "L",
  source: "S",
  type: "circle",
  paint: { color: "#ff0000", radius: 4 },
  representation: {
    windows: [
      { min_zoom: 0, max_zoom: 11, mode: "visual_heatmap" },
      { min_zoom: 11, max_zoom: 24, mode: "point_cluster" },
    ],
  },
} as unknown as MapSpecLayer;

function repSpec(layer: MapSpecLayer = HEAT_CLUSTER_LAYER): MapSpec {
  return {
    version: "1.0",
    sources: { S: { type: "geojson", inlineData: pointFeatures(4) } },
    layers: [layer],
  } as unknown as MapSpec;
}

function layerOn(map: MockMap, id: string): MockLayer {
  const found = map.getLayer(id);
  expect(found, `map layer "${id}" should exist`).toBeDefined();
  return found!; // 上一行 expect 兜底：失败即测试红，此处必在
}

describe("MapSpecRuntime — 表达窗口展开（v1.6 方向 5）", () => {
  let map: MockMap;
  beforeEach(() => {
    map = makeMockMap();
    resetSymbolLawEvidence();
  });

  describe("apply：heatmap[0,11) + point_cluster[11,24)", () => {
    it("主层 = 身份锚（heatmap 型、minzoom=24）+ __rep0 + __clusters + __cluster-count", () => {
      const rt = makeRuntime(map);
      rt.reconcile(repSpec());
      expect(rt.getLastError()).toBeNull();

      const main = layerOn(map, "L");
      expect(main.type).toBe("heatmap");
      expect(main.minzoom).toBe(24);
      expect(main.maxzoom).toBeUndefined();
      // heatmapOnlyPaint：circle-* 键不得残留（MapLibre 会拒绝）
      const paintKeys = Object.keys(main.paint ?? {});
      expect(paintKeys.every((k) => k.startsWith("heatmap-"))).toBe(true);

      const rep0 = layerOn(map, "L__rep0");
      expect(rep0.type).toBe("heatmap");
      expect(rep0.minzoom).toBe(0);
      expect(rep0.maxzoom).toBe(11);
      expect(rep0.paint?.["heatmap-radius"]).toBeDefined();

      const clusters = layerOn(map, "L__clusters");
      expect(clusters.type).toBe("circle");
      expect(clusters.minzoom).toBe(11);
      expect(clusters.maxzoom).toBe(24);
      expect(clusters.filter).toEqual(["has", "point_count"]);

      const count = layerOn(map, "L__cluster-count");
      expect(count.type).toBe("symbol");
      expect(count.minzoom).toBe(11);
      expect(count.maxzoom).toBe(24);
      expect(count.filter).toEqual(["has", "point_count"]);
    });

    it("geojson 源带 cluster:true + clusterMaxZoom=24（窗终点）", () => {
      const rt = makeRuntime(map);
      rt.reconcile(repSpec());
      const src = map.getSource("S") as MockSource;
      expect(src.cluster).toBe(true);
      expect(src.clusterMaxZoom).toBe(24);
      expect(src.clusterRadius).toBe(60);
    });

    it("evidence：representation-window-layer（rep0）在场", () => {
      makeRuntime(map).reconcile(repSpec());
      const ev = getSymbolLawEvidence().events.find((e) => e.kind === "representation-window-layer");
      expect(ev).toBeDefined();
      expect(ev!.detail).toMatchObject({ mode: "visual_heatmap", sublayerId: "L__rep0" });
    });
  });

  describe("reconcile 到不含该层的新 spec：级联清理", () => {
    it("全部子层与源被清（无 ghost）", () => {
      const rt = makeRuntime(map);
      rt.reconcile(repSpec());
      expect(map.getLayer("L__rep0")).toBeDefined();

      rt.reconcile({ version: "1.0", sources: {}, layers: [] } as MapSpec);

      expect(map.getStyle().layers).toEqual([]);
      expect(Object.keys(map._sources)).toEqual([]);
      expect(map._calls.removeLayer).toEqual(
        expect.arrayContaining(["L", "L__clusters", "L__cluster-count", "L__rep0"]),
      );
    });
  });

  describe("窗口计划变更（recompile）：子层整套替换不重复", () => {
    it("heatmap+cluster → 纯 raw 计划：旧子层全部消失", () => {
      const rt = makeRuntime(map);
      rt.reconcile(repSpec());

      const nextLayer: MapSpecLayer = {
        ...HEAT_CLUSTER_LAYER,
        representation: {
          windows: [{ min_zoom: 0, max_zoom: 24, mode: "hidden" }],
        },
      } as unknown as MapSpecLayer;
      rt.reconcile(repSpec(nextLayer));

      // hidden 单窗计划：主层 = 恒空门身份锚，无任何展开子层
      for (const id of ["L__rep0", "L__clusters", "L__cluster-count"]) {
        expect(map.getLayer(id), `ghost sublayer ${id}`).toBeUndefined();
      }
      const main = layerOn(map, "L");
      expect(main.minzoom).toBe(24);
      // hidden 窗 evidence 留痕
      expect(
        getSymbolLawEvidence().events.some((e) => e.kind === "representation-window-hidden"),
      ).toBe(true);
    });
  });

  describe("spec 更新 layout.visibility=none（用户显隐）", () => {
    function hiddenSpec(): MapSpec {
      const layer: MapSpecLayer = {
        ...HEAT_CLUSTER_LAYER,
        layout: { visibility: "none" },
      } as unknown as MapSpecLayer;
      return repSpec(layer);
    }

    it("主层 visibility=none（计划层 layout 增量回落 recompile —— 子层同隐）", () => {
      const rt = makeRuntime(map);
      rt.reconcile(repSpec());

      rt.reconcile(hiddenSpec());

      // v1.6 修复：表达计划的层 layout 增量回落整层 recompile —— 克隆
      // 子层（__rep*）与主层的 visibility 由重建保证联动（零 remove/add
      // 的增量路径会让子层漂移/复活，见 Subagent B 报告 #4）。
      expect(layerOn(map, "L").layout?.visibility).toBe("none");
      for (const subId of ["L__rep0", "L__clusters", "L__cluster-count"]) {
        const sub = layerOn(map, subId);
        if (sub) {
          expect(sub.layout?.visibility ?? "visible").toBe("none");
        }
      }
    });

    // 疑似实现漏洞（测试报告 #4）：layout patch 只对主层 setLayoutProperty，
    // 表达子层（__rep0/__clusters/__cluster-count）不级联 —— 后端驱动的
    // spec 显隐更新会让 scale 子层复活已隐藏的层（app 内用户路径靠
    // map-commands matchMapLayers 另行级联，纯 runtime 契约面漂移；headless
    // 编译产物子层带 visibility:none）。期望：子层同隐。
    it("表达子层同隐（hidden-by-user 不被 scale 子层复活）（已修复：计划层 layout 增量回落 recompile）", () => {
      const rt = makeRuntime(map);
      rt.reconcile(repSpec());
      rt.reconcile(hiddenSpec());
      expect(layerOn(map, "L__rep0").layout?.visibility).toBe("none");
      expect(layerOn(map, "L__clusters").layout?.visibility).toBe("none");
      expect(layerOn(map, "L__cluster-count").layout?.visibility).toBe("none");
    });
  });

  describe("无计划层：行为与旧一致（回归保护）", () => {
    it("无 representation → 无 __rep 子层、源无 cluster、主层无门", () => {
      const rt = makeRuntime(map);
      const layer: MapSpecLayer = {
        id: "L",
        source: "S",
        type: "circle",
        paint: { color: "#ff0000", radius: 4 },
      } as unknown as MapSpecLayer;
      rt.reconcile(repSpec(layer));

      expect(rt.getLastError()).toBeNull();
      expect(map.getStyle().layers.map((l) => l.id)).toEqual(["L"]);
      const main = layerOn(map, "L");
      expect(main.type).toBe("circle");
      expect(main.minzoom).toBeUndefined();
      expect(main.maxzoom).toBeUndefined();
      expect(map.getSource("S")?.cluster).toBeUndefined();
    });
  });

  describe("计划移除后的源级聚合残留（对抗性）", () => {
    // 疑似实现漏洞（测试报告 #5）：refreshRepresentationClusterMap 清空后
    // applySource 只在「需要聚合」时重建源；计划被移除时 clusteredSourceIds
    // 虽被删除，但既有源的 cluster:true/clusterMaxZoom 配置不拆除（注册表现为 appliedClusterMaxZoom 值账本）
    // （MapLibre 不支持原地改配 → 需 removeSource+addSource 重建）。
    // 重建后的纯 circle 层不带 !point_count filter —— 聚簇伪要素会被当
    // 普通点渲染。期望：计划移除 → 源聚合配置被清。
    it("representation 移除 → 源 cluster 配置随之拆除（已修复：applySource 拆配重建）", () => {
      const rt = makeRuntime(map);
      rt.reconcile(repSpec());

      const next = JSON.parse(JSON.stringify(repSpec())) as MapSpec;
      delete (next.layers[0] as MapSpecLayer).representation;
      rt.reconcile(next);

      expect(map.getSource("S")?.cluster).toBeUndefined();
      expect(map.getSource("S")?.clusterMaxZoom).toBeUndefined();
    });

    it("当前行为锁定：计划移除后主层重建为无 filter 的普通 circle 层", () => {
      const rt = makeRuntime(map);
      rt.reconcile(repSpec());

      const next = JSON.parse(JSON.stringify(repSpec())) as MapSpec;
      delete (next.layers[0] as MapSpecLayer).representation;
      rt.reconcile(next);

      const main = layerOn(map, "L");
      expect(main.type).toBe("circle");
      expect(main.filter).toBeUndefined();
      expect(map.getLayer("L__rep0")).toBeUndefined();
    });
  });

  // #1726 项 1（live 路径）：主层「型」与「门」同源。首窗
  // visual_heatmap + 后续 raw 窗时，主层门 = raw 首窗 [11,24)，型必须
  // 仍是 circle —— 此前型取 normalized 首窗 heatmap，raw 窗被上屏成
  // heatmap（与 headless compiler 双路径漂移的根因）。
  describe("首窗 heatmap + 后续 raw 窗：主层型/门同源（#1726）", () => {
    const HEAT_THEN_RAW_LAYER: MapSpecLayer = {
      id: "L",
      source: "S",
      type: "circle",
      paint: { color: "#ff0000", radius: 4 },
      representation: {
        windows: [
          { min_zoom: 0, max_zoom: 11, mode: "visual_heatmap" },
          { min_zoom: 11, max_zoom: 24, mode: "raw" },
        ],
      },
    } as unknown as MapSpecLayer;

    it("主层门 [11,24) 且型为 circle（heatmap 表达由 __rep0 子层承载）", () => {
      const rt = makeRuntime(map);
      rt.reconcile(repSpec(HEAT_THEN_RAW_LAYER));
      expect(rt.getLastError()).toBeNull();

      const main = layerOn(map, "L");
      expect(main.type).toBe("circle");
      expect(main.minzoom).toBe(11);
      expect(main.maxzoom).toBe(24);
      // circle paint 键不得被 heatmapOnlyPaint 改写（无 heatmap-* 残留）
      const paintKeys = Object.keys(main.paint ?? {});
      expect(paintKeys.some((k) => k.startsWith("heatmap-"))).toBe(false);

      const rep0 = layerOn(map, "L__rep0");
      expect(rep0.type).toBe("heatmap");
      expect(rep0.minzoom).toBe(0);
      expect(rep0.maxzoom).toBe(11);
      expect(rep0.paint?.["heatmap-radius"]).toBeDefined();
    });
  });

  describe("raw 多窗计划的展开 parity", () => {
    it("raw[0,11)+cluster[11,14)+raw[14,24)：主层门 [0,11) + rep2 克隆 [14,24) + 簇三子层 [11,14)", () => {
      const layer: MapSpecLayer = {
        id: "L",
        source: "S",
        type: "circle",
        paint: { color: "#ff0000", radius: 4 },
        representation: {
          windows: [
            { min_zoom: 0, max_zoom: 11, mode: "raw" },
            { min_zoom: 11, max_zoom: 14, mode: "point_cluster" },
            { min_zoom: 14, max_zoom: 24, mode: "raw" },
          ],
        },
      } as unknown as MapSpecLayer;
      const rt = makeRuntime(map);
      rt.reconcile(repSpec(layer));

      const main = layerOn(map, "L");
      expect(main.type).toBe("circle");
      expect(main.minzoom).toBe(0);
      expect(main.maxzoom).toBe(11);
      expect(main.filter).toEqual(["!", ["has", "point_count"]]);

      const rep2 = layerOn(map, "L__rep2");
      expect(rep2.minzoom).toBe(14);
      expect(rep2.maxzoom).toBe(24);
      expect(rep2.filter).toEqual(["!", ["has", "point_count"]]);

      expect(layerOn(map, "L__clusters").minzoom).toBe(11);
      expect(layerOn(map, "L__clusters").maxzoom).toBe(14);
      expect(layerOn(map, "L__cluster-count").minzoom).toBe(11);
      expect(layerOn(map, "L__cluster-count").maxzoom).toBe(14);
      expect(map.getSource("S")?.clusterMaxZoom).toBe(14);
    });
  });

  describe("review P1 修复回归", () => {
    it("P1-1：spec 中途新增簇窗 → 源重建带 cluster 配置（不再静默空白）", () => {
      const rt = makeRuntime(map);
      const plain = JSON.parse(JSON.stringify(repSpec())) as MapSpec;
      delete (plain.layers[0] as MapSpecLayer).representation;
      rt.reconcile(plain);
      expect(map.getSource("S")?.cluster).toBeUndefined();

      rt.reconcile(repSpec());
      const src = map.getSource("S") as Record<string, unknown>;
      expect(src.cluster).toBe(true);
      expect(src.clusterMaxZoom).toBe(24);
      // 簇三子层在场且门正确（簇窗 zoom 段不空白）。
      expect(map.getLayer("L__clusters")).toBeDefined();
      expect(map.getLayer("L__cluster-count")).toBeDefined();
      const clusters = layerOn(map, "L__clusters");
      expect(clusters.minzoom).toBe(11);
      expect(clusters.maxzoom).toBe(24);
    });

    it("P1-2：filter patch 对计划层级联（非首窗 zoom 段选择过滤不失效）", () => {
      const layer: MapSpecLayer = {
        id: "L",
        source: "S",
        type: "circle",
        paint: { color: "#ff0000", radius: 4 },
        representation: {
          windows: [
            { min_zoom: 0, max_zoom: 11, mode: "raw" },
            { min_zoom: 11, max_zoom: 14, mode: "hidden" },
            { min_zoom: 14, max_zoom: 24, mode: "raw" },
          ],
        },
      } as unknown as MapSpecLayer;
      const rt = makeRuntime(map);
      rt.reconcile(repSpec(layer));
      const rep2 = layerOn(map, "L__rep2");
      expect(rep2.filter).toBeUndefined();

      const next = JSON.parse(JSON.stringify(repSpec(layer))) as MapSpec;
      (next.layers[0] as MapSpecLayer).filter = ["==", ["get", "v"], 1] as never;
      rt.reconcile(next);

      // 回落 recompile：主层与 rep2 克隆都带精确的 authored filter。
      const main2 = layerOn(map, "L");
      const rep2After = layerOn(map, "L__rep2");
      expect(main2.filter).toEqual(["==", ["get", "v"], 1]);
      expect(rep2After.filter).toEqual(["==", ["get", "v"], 1]);
    });

    it("P1-1 完备性：簇窗终点中途变更 → 源 clusterMaxZoom 值对齐重建", () => {
      // raw[0,11) + cluster[11,end) + raw[end,24)：簇窗终点 = end（变更对象）。
      const layerWith = (end: number): MapSpecLayer => ({
        id: "L",
        source: "S",
        type: "circle",
        paint: { color: "#ff0000", radius: 4 },
        representation: {
          windows: [
            { min_zoom: 0, max_zoom: 11, mode: "raw" },
            { min_zoom: 11, max_zoom: end, mode: "point_cluster" },
            { min_zoom: end, max_zoom: 24, mode: "raw" },
          ],
        },
      } as unknown as MapSpecLayer);
      const rt = makeRuntime(map);
      rt.reconcile(repSpec(layerWith(14)));
      expect((map.getSource("S") as Record<string, unknown>).clusterMaxZoom).toBe(14);

      rt.reconcile(repSpec(layerWith(17)));
      const src = map.getSource("S") as Record<string, unknown>;
      expect(src.clusterMaxZoom).toBe(17); // 值变更触发重建（非只比布尔）
      const clusters = layerOn(map, "L__clusters");
      expect(clusters.minzoom).toBe(11);
      expect(clusters.maxzoom).toBe(17);
    });

    it("P1-2 回归：paint+filter 同 patch → filter 回落不得回退新 paint", () => {
      const base: MapSpecLayer = {
        id: "L",
        source: "S",
        type: "circle",
        paint: { color: "#ff0000", radius: 4 },
        representation: {
          windows: [
            { min_zoom: 0, max_zoom: 11, mode: "raw" },
            { min_zoom: 11, max_zoom: 14, mode: "hidden" },
            { min_zoom: 14, max_zoom: 24, mode: "raw" },
          ],
        },
      } as unknown as MapSpecLayer;
      const rt = makeRuntime(map);
      rt.reconcile(repSpec(base));

      const next = JSON.parse(JSON.stringify(repSpec(base))) as MapSpec;
      const nl = next.layers[0] as unknown as Record<string, unknown>;
      nl.paint = { color: "#00ff00", radius: 4 };
      nl.filter = ["==", ["get", "v"], 1];
      rt.reconcile(next);

      const main2 = layerOn(map, "L");
      const rep2 = layerOn(map, "L__rep2");
      // paint patch 先行回落已用完整新定义重建；filter 回落不得用 stale
      // appliedSpec 合成把 paint 回退成旧值。
      expect(main2.paint?.["circle-color"] ?? main2.paint?.color)
        .not.toBe("#ff0000");
      expect(rep2.filter).toEqual(["==", ["get", "v"], 1]);
    });

    it("P1-2 补面：filter 移除（undefined）→ 子层同步去过滤", () => {
      const base: MapSpecLayer = {
        id: "L",
        source: "S",
        type: "circle",
        paint: { color: "#ff0000", radius: 4 },
        filter: ["==", ["get", "v"], 1],
        representation: {
          windows: [
            { min_zoom: 0, max_zoom: 11, mode: "raw" },
            { min_zoom: 11, max_zoom: 14, mode: "hidden" },
            { min_zoom: 14, max_zoom: 24, mode: "raw" },
          ],
        },
      } as unknown as MapSpecLayer;
      const rt = makeRuntime(map);
      rt.reconcile(repSpec(base));

      const next = JSON.parse(JSON.stringify(repSpec(base))) as MapSpec;
      delete (next.layers[0] as unknown as Record<string, unknown>).filter;
      rt.reconcile(next);

      expect(layerOn(map, "L").filter).toBeUndefined();
      expect(layerOn(map, "L__rep2").filter).toBeUndefined();
    });
  });
});
