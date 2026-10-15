import { describe, it, expect, beforeEach } from "vitest";
import { compileMapSpec } from "./compiler";
import { getSymbolLawEvidence, resetSymbolLawEvidence } from "../map-kit/symbol-law";
import type { MapSpec, MapSpecLayer, MapSpecRepresentationWindow } from "./types";

/**
 * v1.6（方向 5）：compileMapSpec 的表达窗口展开契约测试。
 *
 * spec 形状参考 corpus fixtures（tests/cartography/carto_ir_corpus/*.json：
 * sources.geojson inlineData + layers[].representation.windows）。
 *
 * 版本门历史注记：实现早期 SUPPORTED_MAPSPEC_VERSIONS 未收录 "1.6"
 * （v1.6 spec 被拒编译，Subagent B 报告 #1）；已修复 —— version.ts 词表
 * 含 1.6 + 1.5→1.6 identity upgrader，文末用例正向锁定。fixture 沿用
 * "1.5" 声明（表达窗编译逻辑与 version 无关；1.5 输入按 identity 迁移
 * 到 1.6 语义后驱动全部展开路径）。
 */

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

interface SpecOptions {
  windows?: MapSpecRepresentationWindow[];
  layer?: Partial<MapSpecLayer>;
  extraLayer?: MapSpecLayer;
}

/** 单层点图 spec（默认 5 要素 —— 低于密度自适应阈值，推断保持 native）。 */
function specWith(opts: SpecOptions = {}): MapSpec {
  const layer: MapSpecLayer = {
    id: "L",
    source: "S",
    type: "circle",
    paint: { color: "#ff0000", radius: 4 },
    ...(opts.windows ? { representation: { windows: opts.windows } } : {}),
    ...opts.layer,
  };
  const layers = opts.extraLayer ? [layer, opts.extraLayer] : [layer];
  return {
    version: "1.5",
    view: { center: [116.4, 39.9], zoom: 10 },
    sources: { S: { type: "geojson", inlineData: pointFeatures(5) } },
    layers,
  } as MapSpec;
}

function layerOf(result: ReturnType<typeof compileMapSpec>, id: string): Record<string, any> {
  const found = result.style.layers.find((l) => l.id === id);
  expect(found, `compiled layer "${id}" should exist`).toBeDefined();
  return found as unknown as Record<string, any>;
}

function evidenceKinds(): Array<[string, string | undefined]> {
  return getSymbolLawEvidence().events.map((e) => [e.kind, e.id]);
}

beforeEach(() => {
  resetSymbolLawEvidence();
});

describe("compileMapSpec：a) heatmap[0,11) + point_cluster[11,24)", () => {
  const windows: MapSpecRepresentationWindow[] = [
    { min_zoom: 0, max_zoom: 11, mode: "visual_heatmap" },
    { min_zoom: 11, max_zoom: 24, mode: "point_cluster" },
  ];

  it("主层 = 身份锚（无 raw 窗 → minzoom=24），首窗 heatmap 改写主层型", () => {
    const r = compileMapSpec(specWith({ windows }));
    expect(r.report.success).toBe(true);
    const main = layerOf(r, "L");
    expect(main.minzoom).toBe(24);
    expect(main.maxzoom).toBeUndefined();
    expect(main.type).toBe("heatmap");
  });

  it("存在 ${id}__rep0 heatmap 子层，门 = 首窗 [0,11)", () => {
    const r = compileMapSpec(specWith({ windows }));
    const rep0 = layerOf(r, "L__rep0");
    expect(rep0.type).toBe("heatmap");
    expect(rep0.minzoom).toBe(0);
    expect(rep0.maxzoom).toBe(11);
    expect(rep0.source).toBe("S");
    // 符号律兜底的 heatmap-radius 必在（heatmap 子层缺 radius 不可渲染）
    expect(rep0.paint["heatmap-radius"]).toBeDefined();
    expect((rep0.paint["heatmap-radius"] as unknown[])[0]).toBe("interpolate");
  });

  it("源级聚合配置：cluster:true + clusterMaxZoom=簇窗终点 24", () => {
    const r = compileMapSpec(specWith({ windows }));
    const src = r.style.sources["S"] as Record<string, unknown>;
    expect(src.cluster).toBe(true);
    expect(src.clusterMaxZoom).toBe(24);
  });

  it("evidence：representation-declared + representation-window-layer（rep0）", () => {
    compileMapSpec(specWith({ windows }));
    const kinds = evidenceKinds();
    expect(kinds).toContainEqual(["representation-declared", "L"]);
    expect(kinds).toContainEqual(["representation-window-layer", "L"]);
    const ev = getSymbolLawEvidence().events.find((e) => e.kind === "representation-window-layer")!;
    expect(ev.detail).toMatchObject({ mode: "visual_heatmap", sublayerId: "L__rep0" });
  });

  // 疑似实现漏洞（测试报告 #2）：首窗 heatmap 把编译层型改写为 heatmap 后，
  // 簇三子层门 `layerType === "circle"` 不再成立 → __clusters/__cluster-count
  // 静默缺失（expansion 又无条件跳过 point_cluster 窗），源却被配了
  // cluster:true —— zoom [11,24) 编译产物什么都不渲染。live runtime
  // （runtime.ts addRepresentationSublayersSafe 判 layer.type）会挂出簇三
  // 子层 —— 双路径漂移。期望行为按任务契约（簇窗 → 三子层门 [11,24)）。
  it("簇窗 [11,24) → __clusters/__cluster-count 子层门 [11,24)（已修复）", () => {
    const r = compileMapSpec(specWith({ windows }));
    const clusters = layerOf(r, "L__clusters");
    const count = layerOf(r, "L__cluster-count");
    expect(clusters.minzoom).toBe(11);
    expect(clusters.maxzoom).toBe(24);
    expect(clusters.filter).toEqual(["has", "point_count"]);
    expect(count.minzoom).toBe(11);
    expect(count.maxzoom).toBe(24);
  });
});

describe("compileMapSpec：b) raw[0,11) + point_cluster[11,14) + raw[14,24)", () => {
  const windows: MapSpecRepresentationWindow[] = [
    { min_zoom: 0, max_zoom: 11, mode: "raw" },
    { min_zoom: 11, max_zoom: 14, mode: "point_cluster" },
    { min_zoom: 14, max_zoom: 24, mode: "raw" },
  ];

  it("主层门 = 首个 raw 窗 [0,11)，带未聚类点 filter", () => {
    const r = compileMapSpec(specWith({ windows }));
    const main = layerOf(r, "L");
    expect(main.type).toBe("circle");
    expect(main.minzoom).toBe(0);
    expect(main.maxzoom).toBe(11);
    expect(main.filter).toEqual(["!", ["has", "point_count"]]);
  });

  it("非首 raw 窗 [14,24) → ${id}__rep2 克隆子层（克隆含 filter/paint）", () => {
    const r = compileMapSpec(specWith({ windows }));
    const rep2 = layerOf(r, "L__rep2");
    expect(rep2.type).toBe("circle");
    expect(rep2.minzoom).toBe(14);
    expect(rep2.maxzoom).toBe(24);
    // 聚簇源上的 raw 窗必须继承 !point_count（簇要素不属于 raw 语义）
    expect(rep2.filter).toEqual(["!", ["has", "point_count"]]);
    expect(rep2.paint["circle-color"]).toBe("#ff0000");
  });

  it("cluster 三子层门 = 簇窗 [11,14)；clusterMaxZoom=14", () => {
    const r = compileMapSpec(specWith({ windows }));
    const clusters = layerOf(r, "L__clusters");
    const count = layerOf(r, "L__cluster-count");
    expect(clusters.minzoom).toBe(11);
    expect(clusters.maxzoom).toBe(14);
    expect(clusters.filter).toEqual(["has", "point_count"]);
    expect(count.type).toBe("symbol");
    expect(count.minzoom).toBe(11);
    expect(count.maxzoom).toBe(14);
    const src = r.style.sources["S"] as Record<string, unknown>;
    expect(src.cluster).toBe(true);
    expect(src.clusterMaxZoom).toBe(14);
  });

  it("层清单恰好：__clusters、__cluster-count、__rep2、主层（无多余展开）", () => {
    const r = compileMapSpec(specWith({ windows }));
    expect(r.style.layers.map((l) => l.id).sort()).toEqual(
      ["L", "L__cluster-count", "L__clusters", "L__rep2"].sort(),
    );
  });
});

describe("compileMapSpec：c) hidden 首窗 [0,14) + raw[14,24) + visibility.min_zoom=14", () => {
  const windows: MapSpecRepresentationWindow[] = [
    { min_zoom: 0, max_zoom: 14, mode: "hidden", reason_codes: ["detail_below_zoom"] },
    { min_zoom: 14, max_zoom: 24, mode: "raw" },
  ];

  it("主层门 = max(结构 14, raw 窗 14) = 14，上界 24", () => {
    const r = compileMapSpec(specWith({ windows, layer: { visibility: { min_zoom: 14 } } }));
    expect(r.report.success).toBe(true);
    const main = layerOf(r, "L");
    expect(main.minzoom).toBe(14);
    expect(main.maxzoom).toBe(24);
  });

  it("hidden 窗不产出子层（无 L__rep0）", () => {
    const r = compileMapSpec(specWith({ windows, layer: { visibility: { min_zoom: 14 } } }));
    expect(r.style.layers.some((l) => l.id.startsWith("L__rep"))).toBe(false);
  });

  it("未被结构门裁空的 hidden 窗 → representation-window-hidden evidence（含 reasonCodes）", () => {
    compileMapSpec(specWith({ windows })); // 无 min_zoom 裁剪
    const ev = getSymbolLawEvidence().events.find((e) => e.kind === "representation-window-hidden");
    expect(ev).toBeDefined();
    expect(ev!.id).toBe("L");
    expect(ev!.detail).toMatchObject({ minZoom: 0, maxZoom: 14, reasons: ["detail_below_zoom"] });
  });

  // 疑似实现漏洞（测试报告 #3）：hidden 窗先与结构门求交、交空即 continue，
  // evidence 在交空分支之后才发 —— visibility.min_zoom=14 把 hidden[0,14)
  // 整窗裁空后 representation-window-hidden 静默丢失（导出路径
  // layer_representation_hidden 却照发）。期望：裁空也应留痕（或至少
  // 与导出诊断口径一致）。
  it("hidden 窗被结构门裁空 → 仍应有 representation-window-hidden evidence（已修复）", () => {
    compileMapSpec(specWith({ windows, layer: { visibility: { min_zoom: 14 } } }));
    const ev = getSymbolLawEvidence().events.find((e) => e.kind === "representation-window-hidden");
    expect(ev).toBeDefined();
  });
});

describe("compileMapSpec：d) 无 representation 的旧 spec（v1.5 回归保护）", () => {
  it("编译产物与既有行为一致：circle 主层、无 __rep 子层、源无 cluster", () => {
    const r = compileMapSpec(specWith());
    expect(r.report.success).toBe(true);
    expect(r.style.layers.map((l) => l.id)).toEqual(["L"]);
    const main = layerOf(r, "L");
    expect(main.type).toBe("circle");
    expect(main.minzoom).toBeUndefined();
    expect(main.maxzoom).toBeUndefined();
    const src = r.style.sources["S"] as Record<string, unknown>;
    expect(src.cluster).toBeUndefined();
    expect(src.clusterMaxZoom).toBeUndefined();
  });

  it("无 representation-* evidence（声明优先：无声明零扰动）", () => {
    compileMapSpec(specWith());
    const kinds = evidenceKinds().map(([k]) => k);
    expect(kinds).not.toContain("representation-declared");
    expect(kinds).not.toContain("representation-window-layer");
    expect(kinds).not.toContain("representation-window-hidden");
  });
});

describe("compileMapSpec：e) 结构门 ∩ 窗门", () => {
  it("visibility.max_zoom=10 + raw 窗 [8,24) → 主层 maxzoom=10（门求交）", () => {
    const r = compileMapSpec(
      specWith({
        windows: [
          { min_zoom: 0, max_zoom: 8, mode: "hidden" },
          { min_zoom: 8, max_zoom: 24, mode: "raw" },
        ],
        layer: { visibility: { max_zoom: 10 } },
      }),
    );
    const main = layerOf(r, "L");
    expect(main.minzoom).toBe(8);
    expect(main.maxzoom).toBe(10);
  });

  it("窗门与结构门合成空窗 → 主层退化为 minzoom=24 恒空门 + gate-degenerate evidence", () => {
    const r = compileMapSpec(
      specWith({
        windows: [
          { min_zoom: 0, max_zoom: 11, mode: "visual_heatmap" },
          { min_zoom: 11, max_zoom: 24, mode: "raw" },
        ],
        layer: { visibility: { max_zoom: 6 } },
      }),
    );
    const main = layerOf(r, "L");
    // 首个 raw 窗 [11,24) ∩ 结构门 (-∞,6) = 空 → 身份锚
    expect(main.minzoom).toBe(24);
    const ev = getSymbolLawEvidence().events.find((e) => e.kind === "representation-gate-degenerate");
    expect(ev).toBeDefined();
    expect(ev!.id).toBe("L");
  });

  it("展开窗同样与结构门求交：rep0 maxzoom 收紧到 6", () => {
    const r = compileMapSpec(
      specWith({
        windows: [
          { min_zoom: 0, max_zoom: 11, mode: "visual_heatmap" },
          { min_zoom: 11, max_zoom: 24, mode: "raw" },
        ],
        layer: { visibility: { max_zoom: 6 } },
      }),
    );
    const rep0 = layerOf(r, "L__rep0");
    expect(rep0.minzoom).toBe(0);
    expect(rep0.maxzoom).toBe(6);
  });
});

describe("compileMapSpec：f) 共享源（两层同源，其一有 cluster 窗）", () => {
  const windows: MapSpecRepresentationWindow[] = [
    { min_zoom: 0, max_zoom: 14, mode: "point_cluster" },
    { min_zoom: 14, max_zoom: 24, mode: "raw" },
  ];

  it("不注入源 cluster（兄弟层数据视图不被改写）", () => {
    const r = compileMapSpec(
      specWith({
        windows,
        extraLayer: { id: "L2", source: "S", type: "circle", paint: { color: "#00ff00" } },
      }),
    );
    const src = r.style.sources["S"] as Record<string, unknown>;
    expect(src.cluster).toBeUndefined();
    expect(src.clusterMaxZoom).toBeUndefined();
  });

  it("representation-cluster-degraded evidence（reason=shared_source、id=声明层）", () => {
    compileMapSpec(
      specWith({
        windows,
        extraLayer: { id: "L2", source: "S", type: "circle", paint: { color: "#00ff00" } },
      }),
    );
    const ev = getSymbolLawEvidence().events.find((e) => e.kind === "representation-cluster-degraded");
    expect(ev).toBeDefined();
    expect(ev!.id).toBe("L");
    expect(ev!.detail).toMatchObject({ reason: "shared_source" });
  });

  it("共享源下无 cluster 三子层；raw 窗 [14,24) 仍由主层承载（首个 raw 窗）", () => {
    const r = compileMapSpec(
      specWith({
        windows,
        extraLayer: { id: "L2", source: "S", type: "circle", paint: { color: "#00ff00" } },
      }),
    );
    expect(r.style.layers.some((l) => l.id === "L__clusters")).toBe(false);
    expect(r.style.layers.some((l) => l.id === "L__cluster-count")).toBe(false);
    // [14,24) 是首个 raw 窗 → 主层门；簇窗 [0,14) 降级为 raw 克隆子层
    // （窗口不空转 —— 窗区间有层渲染）。
    const repClone = r.style.layers.find((l) => l.id.startsWith("L__rep"));
    expect(repClone).toBeDefined();
    expect(repClone!.minzoom).toBe(0);
    expect(repClone!.maxzoom).toBe(14);
    const main = layerOf(r, "L");
    expect(main.minzoom).toBe(14);
    expect(main.maxzoom).toBe(24);
  });

  it("显式 cluster 源配置恒优先于窗推断（clusterMaxZoom 不被窗终点改写）", () => {
    const spec = specWith({ windows });
    (spec.sources.S as { cluster?: { maxzoom?: number; radius?: number } }).cluster = { maxzoom: 9 };
    const r = compileMapSpec(spec);
    const src = r.style.sources["S"] as Record<string, unknown>;
    expect(src.cluster).toBe(true);
    expect(src.clusterMaxZoom).toBe(9);
  });
});

describe("compileMapSpec：显式计划优先于密度推断", () => {
  it("有非平凡计划的层不做密度自适应（大 featureCount 也不改写 cluster/heatmap）", () => {
    const spec = specWith({
      windows: [
        { min_zoom: 0, max_zoom: 24, mode: "hidden" },
      ],
    });
    // 3 万点：无声明时会触发 heatmap 密度改写；声明 hidden 平凡窗后主层仍 circle
    (spec.sources.S.inlineData as { features: unknown[] }).features = pointFeatures(30000).features;
    const r = compileMapSpec(spec);
    const main = layerOf(r, "L");
    expect(main.type).toBe("circle");
    const src = r.style.sources["S"] as Record<string, unknown>;
    expect(src.cluster).toBeUndefined();
    const kinds = evidenceKinds().map(([k]) => k);
    expect(kinds).not.toContain("density-switch");
  });
});

// ── 版本协商门（疑似实现漏洞，测试报告 #1）───────────────────────────────
// 本分支把 MapSpec 契约扩到 v1.6（layer.representation.windows），后端
// KNOWN_VERSIONS 与 contract_manifest.json 均含 "1.6"，前端
// contract-manifest.ts 的 CONTRACT_MAPSPEC_VERSIONS 也含 "1.6"——但渲染端
// 协商词表 lib/carto-ir/version.ts SUPPORTED_MAPSPEC_VERSIONS 仍止步
// "1.5"（无 1.5→1.6 upgrader）。结果：version:"1.6" 的 spec 被
// compileMapSpec / MapSpecRuntime 双双 fail-safe 拒绝，表达窗特性对合法
// v1.6 spec 完全不可达。期望：v1.6 spec 正常编译。
describe("compileMapSpec：v1.6 版本门", () => {
  it('version:"1.6" 的 spec 应被支持（已修复）', () => {
    const spec = specWith({
      windows: [
        { min_zoom: 0, max_zoom: 11, mode: "visual_heatmap" },
        { min_zoom: 11, max_zoom: 24, mode: "point_cluster" },
      ],
    });
    spec.version = "1.6";
    const r = compileMapSpec(spec);
    expect(r.report.success).toBe(true);
    expect(r.style.layers.some((l) => l.id === "L__rep0")).toBe(true);
  });
});
