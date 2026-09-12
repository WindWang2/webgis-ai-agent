import { describe, it, expect, beforeEach, vi } from "vitest";
import { MapSpecRuntime } from "./runtime";
import type { MapSpec } from "@/lib/mapspec-compiler/types";
import {
  getPerfCounters,
  resetPerfCounters,
} from "@/lib/utils/perf-counters";
import {
  getSymbolLawEvidence,
  resetSymbolLawEvidence,
} from "@/lib/map-kit/symbol-law";

/**
 * AC-06 (ADR-0155) P3/P7 — 属性级增量更新的运行时事件计数断言。
 *
 * 门禁语义（§5）：
 *  - 改色（paint 变更）必须走 setPaintProperty，**零 removeLayer/addLayer**；
 *  - 增量 patch 失败时回落 recompile（安全网），有降级计数 + evidence；
 *  - layout 变更走 setLayoutProperty；filter 仍走 setFilter（V4 快路径）。
 */

function makeMockMap() {
  const layers: any[] = [];
  const sources: Record<string, any> = {};
  const calls = {
    removeLayer: [] as string[],
    addLayer: [] as string[],
    moveLayer: [] as string[],
    setPaintProperty: [] as Array<{ id: string; key: string; value: unknown }>,
    setLayoutProperty: [] as Array<{ id: string; key: string; value: unknown }>,
    setFilter: [] as Array<{ id: string; filter: unknown }>,
  };
  return {
    layers,
    sources,
    calls,
    isStyleLoaded: () => true,
    getStyle: () => ({ layers, sources }),
    getSource: (id: string) => sources[id],
    addSource: (id: string, def: any) => {
      sources[id] = def;
    },
    removeSource: (id: string) => {
      delete sources[id];
    },
    getLayer: (id: string) => layers.find((l) => l.id === id),
    addLayer: (def: any) => {
      layers.push(def);
      calls.addLayer.push(def.id);
    },
    removeLayer: (id: string) => {
      const i = layers.findIndex((l) => l.id === id);
      if (i >= 0) layers.splice(i, 1);
      calls.removeLayer.push(id);
    },
    moveLayer: (id: string) => {
      calls.moveLayer.push(id);
    },
    setPaintProperty: (id: string, key: string, value: unknown) => {
      calls.setPaintProperty.push({ id, key, value });
      const layer = layers.find((l) => l.id === id);
      if (layer) layer.paint[key] = value;
    },
    setLayoutProperty: (id: string, key: string, value: unknown) => {
      calls.setLayoutProperty.push({ id, key, value });
    },
    setFilter: (id: string, filter: unknown) => {
      calls.setFilter.push({ id, filter });
    },
    _calls: calls,
  };
}

function fc(n: number) {
  return {
    type: "FeatureCollection",
    features: Array.from({ length: n }, (_, i) => ({
      type: "Feature",
      properties: { value: i },
      geometry: { type: "Point", coordinates: [1 + i * 1e-5, 1] },
    })),
  };
}

function spec(color: string, opacity: number | undefined): MapSpec {
  return {
    version: "1.0",
    sources: { pts: { type: "geojson", inlineData: fc(40) } },
    layers: [
      {
        id: "L__point",
        source: "pts",
        type: "circle",
        paint: {
          "circle-color": color,
          ...(opacity !== undefined ? { "circle-opacity": opacity } : {}),
        },
      } as MapSpec["layers"][number],
    ],
  };
}

describe("AC-06 P3: 属性级增量更新（事件计数）", () => {
  beforeEach(() => {
    resetPerfCounters();
    resetSymbolLawEvidence();
  });

  it("改色零 remove/add：paint 变更走 setPaintProperty", () => {
    const map = makeMockMap();
    const rt = new MapSpecRuntime(map as never);
    rt.reconcile(spec("#ff0000", 0.8));
    const baseline = map._calls;

    rt.reconcile(spec("#00ff00", 0.8));

    // 零 remove/add —— 改色不再整层闪烁。
    expect(baseline.removeLayer.length).toBe(0);
    expect(baseline.addLayer.length).toBe(1); // 初次挂载那一次
    expect(baseline.setPaintProperty).toEqual([
      { id: "L__point", key: "circle-color", value: "#00ff00" },
    ]);
    const counters = getPerfCounters();
    expect(counters.layerPaintPatches).toBe(1);
    expect(counters.layerRemoves).toBe(0);
    expect(counters.recompileFallbacks).toBe(0);
    // appliedSpec 前进（lastError 未被污染）。
    expect(rt.getLastError()).toBeNull();
    expect(rt.getAppliedSpec()?.layers[0].paint).toMatchObject({ "circle-color": "#00ff00" });
  });

  it("paint 删除（next 缺键）→ 符号律兜底接管（高密度下 0.8→0.65）", () => {
    // AC-06 语义：显式键删除不是复位 MapLibre 默认 1，而是落到符号律密度
    // 默认值（§0.5：后端未下发的键按默认值兜底）。两个 spec 共享同一源
    // （源不变 → 不触发 recompile，纯 paint patch 通道）。
    const map = makeMockMap();
    const rt = new MapSpecRuntime(map as never);
    const sharedFc = fc(9000);
    const withExplicit: MapSpec = {
      version: "1.0",
      sources: { pts: { type: "geojson", inlineData: sharedFc } },
      layers: [
        {
          id: "L__point",
          source: "pts",
          type: "circle",
          paint: { "circle-color": "#ff0000", "circle-opacity": 0.8 },
        } as MapSpec["layers"][number],
      ],
    };
    const withoutOpacity: MapSpec = {
      version: "1.0",
      sources: { pts: { type: "geojson", inlineData: sharedFc } },
      layers: [
        {
          id: "L__point",
          source: "pts",
          type: "circle",
          paint: { "circle-color": "#ff0000" },
        } as MapSpec["layers"][number],
      ],
    };
    rt.reconcile(withExplicit);
    map._calls.setPaintProperty.length = 0;
    rt.reconcile(withoutOpacity);
    expect(map._calls.setPaintProperty).toEqual([
      { id: "L__point", key: "circle-opacity", value: 0.65 },
    ]);
    expect(map._calls.removeLayer.length).toBe(0);
  });

  it("layout 变更走 setLayoutProperty（visibility 翻转零churn）", () => {
    const map = makeMockMap();
    const rt = new MapSpecRuntime(map as never);
    const visible = spec("#ff0000", 0.8);
    rt.reconcile(visible);
    const hidden = spec("#ff0000", 0.8);
    (hidden.layers[0] as any).layout = { visibility: "none" };
    rt.reconcile(hidden);
    expect(map._calls.removeLayer.length).toBe(0);
    expect(map._calls.setLayoutProperty).toEqual([
      { id: "L__point", key: "visibility", value: "none" },
    ]);
    expect(getPerfCounters().layerLayoutPatches).toBe(1);
  });

  it("setPaintProperty 被拒 → 回落 recompile（remove+add）+ 降级计数 + evidence", () => {
    const map = makeMockMap();
    const rt = new MapSpecRuntime(map as never);
    rt.reconcile(spec("#ff0000", 0.8));
    map.setPaintProperty = vi.fn(() => {
      throw new Error("unsupported expression");
    });

    rt.reconcile(spec("#00ff00", 0.8));

    // 安全网：remove + add 保证收敛。
    expect(map._calls.removeLayer).toEqual(["L__point"]);
    expect(map._calls.addLayer).toEqual(["L__point", "L__point"]);
    expect(getPerfCounters().recompileFallbacks).toBe(1);
    const ev = getSymbolLawEvidence();
    expect(ev.counts["incremental-fallback"]).toBe(1);
    expect(
      ev.events.some(
        (e) => e.kind === "incremental-fallback" && e.id === "L__point",
      ),
    ).toBe(true);
    // 回落成功 → lastError 干净，appliedSpec 前进。
    expect(rt.getLastError()).toBeNull();
    expect(rt.getAppliedSpec()?.layers[0].paint).toMatchObject({ "circle-color": "#00ff00" });
  });

  it("层不在图上（如 style 被换）→ paint patch 走 add 收敛并计回落", () => {
    const map = makeMockMap();
    const rt = new MapSpecRuntime(map as never);
    rt.reconcile(spec("#ff0000", 0.8));
    // 模拟外部把层拆走（不经 runtime）。
    map.layers.length = 0;
    map._calls.removeLayer.length = 0;
    map._calls.addLayer.length = 0;

    rt.reconcile(spec("#00ff00", 0.8));

    expect(map._calls.addLayer).toEqual(["L__point"]);
    expect(getPerfCounters().recompileFallbacks).toBe(1);
    expect(rt.getLastError()).toBeNull();
  });

  it("P1: 后端未下发符号键时按符号律兜底（要素数已知）+ evidence", () => {
    const map = makeMockMap();
    const rt = new MapSpecRuntime(map as never);
    const bare: MapSpec = {
      version: "1.0",
      sources: { pts: { type: "geojson", inlineData: fc(9000) } },
      layers: [{ id: "L__point", source: "pts", type: "circle", paint: { "circle-color": "#ff0000" } }],
    };
    rt.reconcile(bare);
    const added = map.layers.find((l) => l.id === "L__point");
    // 密度 9000：radius=zoom 表达式、opacity 下修 0.65 —— 非静默缺失。
    expect(Array.isArray(added?.paint["circle-radius"])).toBe(true);
    expect(added?.paint["circle-opacity"]).toBeCloseTo(0.65, 5);
    const ev = getSymbolLawEvidence();
    expect(
      ev.events.some((e) => e.kind === "law-applied" && e.id === "L__point"),
    ).toBe(true);
  });
});
