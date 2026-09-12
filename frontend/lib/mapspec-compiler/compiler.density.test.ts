import { describe, expect, it, beforeEach } from "vitest";
import { compileMapSpec } from "./compiler";
import type { MapSpec } from "./types";
import {
  getSymbolLawEvidence,
  resetSymbolLawEvidence,
} from "@/lib/map-kit/symbol-law";

/**
 * AC-06 (ADR-0155) P2 — 密度自适应表达切换（headless 编译路径）。
 *
 * 阈值以 VIEWPORT_RENDER_BUDGET=5000 / MVT 5000 为基准线（§0.5）：
 *  - 点层 ≥ 5000 且无显式 cluster → 源自动聚合 + __clusters 子层 + evidence；
 *  - 点层 ≥ 20000 → 编译为 heatmap 表达（spec 层型不变）；
 *  - 显式 cluster 配置优先于自适应（显式意图永远赢）。
 */

function pointSpec(n: number, extra?: Partial<MapSpec["layers"][number]>): MapSpec {
  return {
    version: "1.0",
    sources: {
      pts: {
        type: "geojson",
        inlineData: {
          type: "FeatureCollection",
          features: Array.from({ length: n }, (_, i) => ({
            type: "Feature",
            properties: { value: i },
            geometry: { type: "Point", coordinates: [1 + i * 1e-6, 1] },
          })),
        },
      },
    },
    layers: [
      {
        id: "L",
        source: "pts",
        type: "circle",
        paint: { color: "#123456" },
        ...extra,
      } as MapSpec["layers"][number],
    ],
  };
}

describe("AC-06 P2: 密度自适应切换", () => {
  beforeEach(() => resetSymbolLawEvidence());

  it("6000 点（≥5000 基准线）→ 源自动聚合 + __clusters 子层 + evidence", () => {
    const result = compileMapSpec(pointSpec(6000));
    expect(result.style.sources.pts.cluster).toBe(true);
    const ids = result.style.layers.map((l: any) => l.id);
    expect(ids).toContain("L__clusters");
    expect(ids).toContain("L__cluster-count");
    // 主层排除簇点。
    const main = result.style.layers.find((l: any) => l.id === "L");
    expect(main.filter).toEqual(["!", ["has", "point_count"]]);
    const snap = getSymbolLawEvidence();
    expect(snap.counts["density-switch"]).toBe(1);
    const sw = snap.events.find((e) => e.kind === "density-switch");
    expect((sw?.detail as any)?.to).toBe("cluster");
  });

  it("25000 点（≥heatmap 阈值）→ 编译为 heatmap 表达 + 符号律半径", () => {
    const result = compileMapSpec(pointSpec(25000));
    const lyr = result.style.layers.find((l: any) => l.id === "L");
    expect(lyr.type).toBe("heatmap");
    expect(lyr.paint["heatmap-radius"][0]).toBe("interpolate");
    expect(lyr.paint["heatmap-radius"][2]).toEqual(["zoom"]);
    const snap = getSymbolLawEvidence();
    const sw = snap.events.find((e) => e.kind === "density-switch");
    expect((sw?.detail as any)?.to).toBe("heatmap");
  });

  it("显式 cluster 配置优先（不产生 density-switch evidence）", () => {
    const spec = pointSpec(6000);
    (spec.sources.pts as any).cluster = { radius: 30, maxzoom: 12 };
    const result = compileMapSpec(spec);
    expect(result.style.sources.pts.cluster).toBe(true);
    expect(result.style.sources.pts.clusterRadius).toBe(30);
    const snap = getSymbolLawEvidence();
    expect(snap.counts["density-switch"] ?? 0).toBe(0);
  });

  it("稀疏点层保持 native + presentation-decision evidence", () => {
    const result = compileMapSpec(pointSpec(500));
    expect(result.style.sources.pts.cluster).toBeUndefined();
    const ids = result.style.layers.map((l: any) => l.id);
    expect(ids).not.toContain("L__clusters");
    const snap = getSymbolLawEvidence();
    expect(
      snap.events.some(
        (e) => e.kind === "presentation-decision" && (e.detail as any).mode === "native",
      ),
    ).toBe(true);
  });

  it("url 源（要素数未知）不做密度裁决", () => {
    const spec: MapSpec = {
      version: "1.0",
      sources: { pts: { type: "geojson", url: "https://x.test/data.geojson" } },
      layers: [{ id: "L", source: "pts", type: "circle", paint: { color: "#111" } } as any],
    };
    const result = compileMapSpec(spec);
    expect(result.style.sources.pts.cluster).toBeUndefined();
    const snap = getSymbolLawEvidence();
    expect(snap.counts["density-switch"] ?? 0).toBe(0);
  });
});
