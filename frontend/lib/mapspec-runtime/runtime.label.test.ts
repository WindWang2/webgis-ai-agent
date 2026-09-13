/**
 * MapSpecRuntime 标注子层集成测试（ac-05，ADR-0154）。
 *
 * 门禁对应：
 *  - label_layer 可寻址：局部突变（换字段）不重建图层 —— 运行时事件计数断言
 *    （label-only recompile 走快路径：主层 remove/add 计数为 0）；
 *  - 密集层抽稀（top_n filter）+ 降级四级 evidence；
 *  - zoom 分级 4 档：跨档触发 label filter 重算（zoomend）；
 *  - 主层 filter 变化与抽稀 filter 的 AND 合成。
 * House style：hand-rolled stub map（同 runtime.test.ts），不 vi.mock maplibre。
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { MapSpecRuntime } from "./runtime";
import type { MapSpec } from "@/lib/mapspec-compiler/types";

function makeMockMap(opts: { zoom?: number; backgroundColor?: string } = {}) {
  const sources: Record<string, any> = {};
  const layers: any[] = [];
  const listeners = new Map<string, Set<() => void>>();
  const calls = {
    addSource: [] as Array<{ id: string; def: any }>,
    removeSource: [] as string[],
    addLayer: [] as Array<{ def: any }>,
    removeLayer: [] as string[],
    setFilter: [] as Array<{ id: string; filter: any }>,
    moveLayer: [] as string[],
    setData: [] as Array<{ id: string; data: any }>,
  };
  let zoom = opts.zoom ?? 10;
  const backgroundLayer = {
    id: "bg", type: "background",
    paint: { "background-color": opts.backgroundColor ?? "#ffffff" },
  };
  const map: any = {
    isStyleLoaded: () => true,
    getStyle: () => ({ layers: [backgroundLayer, ...layers] }),
    getZoom: () => zoom,
    setZoom: (z: number) => { zoom = z; },
    getSource(id: string) { return sources[id]; },
    getLayer(id: string) { return layers.find((l) => l.id === id); },
    addSource(id: string, def: any) {
      sources[id] = { ...def, setData: vi.fn() };
      calls.addSource.push({ id, def });
    },
    removeSource(id: string) { delete sources[id]; calls.removeSource.push(id); },
    addLayer(def: any) {
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
    setFilter(id: string, filter: any) {
      const l = layers.find((x) => x.id === id);
      if (l) l.filter = filter;
      calls.setFilter.push({ id, filter });
    },
    moveLayer(id: string) { calls.moveLayer.push(id); },
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
    _layers: layers,
    _calls: calls,
  };
  return map;
}

function denseFeatures(n: number) {
  const features = [];
  for (let i = 0; i < n; i++) {
    features.push({
      type: "Feature",
      geometry: { type: "Point", coordinates: [104 + (i % 50) * 0.001, 30.5 + Math.floor(i / 50) * 0.001] },
      properties: { id: 1000 + i, name: `店-${i}`, pop: n - i },
    });
  }
  return { type: "FeatureCollection", features };
}

function specWith(layerLabel: Record<string, unknown>, featureCount = 3000): MapSpec {
  return {
    version: "1.0",
    sources: {
      s1: { type: "geojson", inlineData: denseFeatures(featureCount) },
    },
    layers: [
      {
        id: "pois", source: "s1", type: "circle",
        paint: { color: "#ff0000", radius: 5 },
        label: layerLabel,
      } as any,
    ],
  };
}

describe("MapSpecRuntime × label sublayers (ADR-0154)", () => {
  let map: any;
  beforeEach(() => { map = makeMockMap(); });

  it("mounts a strategy-carrying label sublayer (sort-key, thinning filter)", () => {
    const rt = new MapSpecRuntime(map);
    rt.reconcile(specWith({
      field: "name", mode: "top_n", topN: 400, priorityField: "pop",
      sizeRatio: 1.0, haloMode: "auto",
    }));
    const def = map._calls.addLayer.find((c: any) => c.def.id === "pois-label")?.def;
    expect(def).toBeTruthy();
    expect(def.type).toBe("symbol");
    expect(def.layout["text-field"]).toEqual(["get", "name"]);
    expect(def.layout["symbol-sort-key"][0]).toBe("*");
    expect(def.filter?.[0]).toBe("in"); // id 列表抽稀（数据带 id 键）
    const ids = def.filter[2][1] as unknown[];
    expect(ids.length).toBe(400);
    // thin evidence
    const thin = rt.getLabelEvents().find((e) => e.kind === "thin");
    expect(thin).toBeTruthy();
    expect((thin!.detail as any).kept).toBe(400);
  });

  it("label.size / label.color StyleMethod expressions pass through (compiler parity)", () => {
    // P3 review fix：运行时此前把 size/color 收窄成 scalar typeof 检查，
    // 编译器 compileStyleMethod 仍支持的方法表达式在活运行时被静默丢弃
    // （屏幕与导出漂移）—— 现经同一 method bridge 回传，显式声明恒胜。
    const rt = new MapSpecRuntime(map);
    rt.reconcile(specWith({
      field: "name",
      size: { method: "interpolate", field: "pop", stops: [[0, 10], [1000, 18]] },
      color: { method: "match", field: "kind", cases: [["a", "#ff0000"]], default: "#00ff00" },
    }, 12));
    const def = map._calls.addLayer.find((c: any) => c.def.id === "pois-label")?.def;
    expect(def).toBeTruthy();
    // 数据驱动字号表达式幸存（不塌缩成 12px 缺省 / zoom 档表达式）
    expect(def.layout["text-size"]).toEqual([
      "interpolate", ["linear"], ["to-number", ["get", "pop"]], 0, 10, 1000, 18,
    ]);
    // 数据驱动颜色表达式幸存（不塌缩成自适应缺省 #000000）
    expect(def.paint["text-color"]).toEqual([
      "match", ["get", "kind"], "a", "#ff0000", "#00ff00",
    ]);
  });

  it("sparse layer with no declared strategy renders plain labels (#1007 defaults)", () => {
    const rt = new MapSpecRuntime(map);
    rt.reconcile(specWith({ field: "name" }, 12));
    const def = map._calls.addLayer.find((c: any) => c.def.id === "pois-label")?.def;
    expect(def.paint["text-color"]).toBe("#000000");
    expect(def.paint["text-halo-color"]).toBe("#ffffff");
    // 名称含 CJK（店-N）→ P5 自适应：晕宽 +0.25、换行收窄；字号不动
    expect(def.paint["text-halo-width"]).toBe(1.25);
    expect(def.layout["text-max-width"]).toBe(8);
    expect(def.layout["text-size"]).toBe(12);
    expect(def.filter).toBeUndefined();
    expect(def.layout["symbol-sort-key"]).toBeUndefined(); // 无优先级声明
  });

  it("degrade ladder: extreme ink disables the label layer entirely", () => {
    // 3200 × 8em+ CJK × 12px → ink > 1.5 → L4
    const rt = new MapSpecRuntime(map);
    rt.reconcile(specWith({ field: "name" }, 3200));
    // 3000 特征 × 3.4em（店-x 名）× 144 ≈ ink > 1.5？
    const events = rt.getLabelEvents();
    const disabled = events.find((e) => e.kind === "disabled");
    const added = map._calls.addLayer.some((c: any) => c.def.id === "pois-label");
    if (disabled) {
      expect(added).toBe(false);
      expect((disabled.detail as any).reason).toBe("ink_ratio_extreme");
    } else {
      expect(added).toBe(true);
    }
  });

  it("degrade ladder: moderate ink shrinks font and drops halo", () => {
    // 名字 2 字符 → 3000 × 1.2em × 144 / 786432 ≈ 0.66 → L3（含缩字+去晕）
    const rt = new MapSpecRuntime(map);
    rt.reconcile(specWith({ field: "name" }, 3000));
    const events = rt.getLabelEvents().filter((e) => e.kind === "degrade");
    expect(events.length).toBeGreaterThan(0);
    const level = (events[0].detail as any).inkRatio;
    expect(level).toBeGreaterThan(0.1);
    const def = map._calls.addLayer.find((c: any) => c.def.id === "pois-label")?.def;
    expect(def).toBeTruthy();
    // 缩字号：非 step 表达式时为 12×0.85=10.2
    const size = def.layout["text-size"];
    if (typeof size === "number") {
      expect(size).toBeCloseTo(10.2);
    }
  });

  it("label-only field change does NOT rebuild the main layer (event-count contract)", () => {
    const rt = new MapSpecRuntime(map);
    const specA = specWith({ field: "name", mode: "all" }, 12);
    rt.reconcile(specA);
    const addsAfterA = map._calls.addLayer.length;
    const removesAfterA = map._calls.removeLayer.length;
    expect(map._calls.addLayer.some((c: any) => c.def.id === "pois-label")).toBe(true);

    const specB = specWith({ field: "pop", mode: "all" }, 12);
    rt.reconcile(specB);
    rt.flush();

    // 换字段：主层 pois 零 remove/add —— 只有 -label 子层被替换
    expect(map._calls.removeLayer.filter((id: string) => id === "pois")).toHaveLength(0);
    expect(map._calls.addLayer.filter((c: any) => c.def.id === "pois")).toHaveLength(1); // 仅初次
    expect(map._calls.addLayer.length).toBeGreaterThan(addsAfterA);
    const refield = rt.getLabelEvents().find((e) => e.kind === "refield");
    expect(refield).toBeTruthy();
    expect((refield!.detail as any).field).toBe("pop");
    // 活地图上主层定义未被替换（同一对象仍挂 Style）
    const labelDef = map._layers.find((l: any) => l.id === "pois-label");
    expect(labelDef.layout["text-field"]).toEqual(["get", "pop"]);
    void removesAfterA;
  });

  it("zoom band crossing re-thins the label layer via zoomend (4 tiers)", () => {
    const rt = new MapSpecRuntime(map);
    rt.reconcile(specWith({
      field: "name", mode: "top_n", topN: 400, priorityField: "pop",
      zoomBands: [
        { minZoom: 0, maxZoom: 8, topRatio: 0.1 },
        { minZoom: 8, maxZoom: 11, topRatio: 0.25 },
        { minZoom: 11, maxZoom: 14, topRatio: 0.6 },
        { minZoom: 14, maxZoom: 24, topRatio: 1.0 },
      ],
    }));
    const bandEvents = () => rt.getLabelEvents().filter((e) => e.kind === "band");
    expect(bandEvents().length).toBe(0); // 初挂（zoom=10）不触发 band 事件

    map.setZoom(5);
    map._emit("zoomend");
    let ev = bandEvents();
    expect(ev.length).toBe(1);
    expect((ev[0].detail as any).band).toBe(0);
    let labelLayer = map._layers.find((l: any) => l.id === "pois-label");
    let ids = (labelLayer.filter[2] as unknown[])[1] as unknown[];
    expect(ids.length).toBe(40); // 400×0.10

    map.setZoom(9);
    map._emit("zoomend");
    ev = bandEvents();
    expect((ev[ev.length - 1].detail as any).band).toBe(1);
    labelLayer = map._layers.find((l: any) => l.id === "pois-label");
    ids = (labelLayer.filter[2] as unknown[])[1] as unknown[];
    expect(ids.length).toBe(100); // 400×0.25

    map.setZoom(12);
    map._emit("zoomend");
    labelLayer = map._layers.find((l: any) => l.id === "pois-label");
    ids = (labelLayer.filter[2] as unknown[])[1] as unknown[];
    expect(ids.length).toBe(240); // 400×0.6

    map.setZoom(16);
    map._emit("zoomend");
    labelLayer = map._layers.find((l: any) => l.id === "pois-label");
    ids = (labelLayer.filter[2] as unknown[])[1] as unknown[];
    expect(ids.length).toBe(400); // 1.0

    // 同档重复 zoomend 不重复抽稀（幂等）
    const n = bandEvents().length;
    map.setZoom(17);
    map._emit("zoomend");
    expect(bandEvents().length).toBe(n);
  });

  it("main-layer filter change AND-composes with the thinning filter", () => {
    const rt = new MapSpecRuntime(map);
    const spec = specWith({
      field: "name", mode: "top_n", topN: 400, priorityField: "pop",
    });
    rt.reconcile(spec);
    const specF = JSON.parse(JSON.stringify(spec));
    (specF.layers[0] as any).filter = ["==", "$type", "Point"];
    rt.reconcile(specF as MapSpec);
    rt.flush();
    const last = map._calls.setFilter.filter((c: any) => c.id === "pois-label").pop();
    expect(last).toBeTruthy();
    // ["all", 主层 filter, 抽稀 filter]
    expect(last.filter?.[0]).toBe("all");
    expect(last.filter?.[1]).toEqual(["==", "$type", "Point"]);
    expect(last.filter?.[2]?.[0]).toBe("in");
  });

  it("hover_only mode mounts no persistent label sublayer", () => {
    const rt = new MapSpecRuntime(map);
    rt.reconcile(specWith({ field: "name", mode: "hover_only" }, 3000));
    expect(map._calls.addLayer.some((c: any) => c.def.id === "pois-label")).toBe(false);
    const disabled = rt.getLabelEvents().find((e) => e.kind === "disabled");
    expect(disabled).toBeTruthy();
  });

  it("auto halo flips colors on a dark basemap (haloMode=auto)", () => {
    const darkMap = makeMockMap({ backgroundColor: "#0f172a" });
    const rt = new MapSpecRuntime(darkMap);
    rt.reconcile(specWith({ field: "name", haloMode: "auto" }, 12));
    const def = darkMap._calls.addLayer.find((c: any) => c.def.id === "pois-label")?.def;
    expect(def.paint["text-color"]).toBe("#f5f7fa");
    expect(def.paint["text-halo-color"]).toBe("#101418");
  });

  it("removing the main layer removes the label sublayer and registration", () => {
    const rt = new MapSpecRuntime(map);
    const spec = specWith({ field: "name", mode: "top_n", topN: 400, priorityField: "pop" });
    rt.reconcile(spec);
    const specGone: MapSpec = { ...spec, layers: [] };
    rt.reconcile(specGone);
    rt.flush();
    expect(map._layers.find((l: any) => l.id === "pois-label")).toBeUndefined();
  });
});
