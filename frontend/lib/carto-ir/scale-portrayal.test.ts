/**
 * G27 前端契约测试 —— 与 backend pytest **消费同一份** scale_portrayal_cases.json。
 *
 * 断言的是真实前端消费链（`normalizeLabelStrategy` → `bandTopRatio` /
 * `activeBandIndex`），不是自造的 stub：corpus 的 4 档来自后端
 * `spec_label_zoom_bands()` 投影，逐个 case 的 zoom 边界在两侧必须同档。
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";

import {
  activeBandIndex,
  bandTopRatio,
  normalizeLabelStrategy,
} from "@/lib/mapspec-runtime/label-layout";
import {
  G27_LABEL_ZOOM_BANDS,
  G27_POLICY_NAMESPACE,
  G27_SCALE_DISCLOSURES,
  G27_SCALE_POLICY_MODES,
  G27_SCALE_POLICY_UNITS,
  G27_SCALE_POLICY_VERSION,
  hasScalePortrayalConsumer,
  isScalePortrayalApplied,
  readScalePortrayalPolicy,
  resolveScalePortrayalView,
} from "./scale-portrayal";

interface CorpusCase {
  id: string;
  zoom: number;
  layer: Record<string, unknown>;
  policy: Record<string, unknown> | null;
  available_fields: string[];
  options?: Record<string, unknown>;
  expected: Record<string, unknown>;
}

interface Corpus {
  corpus_version: string;
  policy_modes: string[];
  units: string[];
  label_zoom_bands: Array<{
    minZoom: number;
    maxZoom: number;
    topRatio: number;
    sizeRatio: number;
  }>;
  cases: CorpusCase[];
}

const corpusPath = fileURLToPath(
  new URL(
    "../../../tests/cartography/carto_ir_corpus/scale_portrayal_cases.json",
    import.meta.url,
  ),
);
const corpus = JSON.parse(readFileSync(corpusPath, "utf8")) as Corpus;

/** 模拟 converter 已落笔的生产状态：4 档 + opt-in policy。 */
function layerFor(c: CorpusCase, withBands = true) {
  return {
    ...c.layer,
    ...(withBands ? { label: { field: "name", zoomBands: corpus.label_zoom_bands } } : {}),
    scale_profile: c.policy === null ? {} : { [G27_POLICY_NAMESPACE]: c.policy },
  };
}

describe("G27 共享契约语料（backend ≡ frontend）", () => {
  it("词表 / 版本 / 4 档投影逐位一致", () => {
    expect(corpus.corpus_version).toBe(G27_SCALE_POLICY_VERSION);
    expect(corpus.policy_modes).toEqual([...G27_SCALE_POLICY_MODES]);
    expect(corpus.units).toEqual([...G27_SCALE_POLICY_UNITS]);
    expect(corpus.label_zoom_bands).toEqual([...G27_LABEL_ZOOM_BANDS]);
    expect(G27_SCALE_DISCLOSURES).toHaveLength(4);
  });

  it("4 档覆盖 [0,24) 全域、边界连续、系数单调不降（A04）", () => {
    const bands = corpus.label_zoom_bands;
    expect(bands).toHaveLength(4);
    expect(bands[0].minZoom).toBe(0);
    expect(bands[bands.length - 1].maxZoom).toBe(24);
    for (let i = 0; i < bands.length - 1; i++) {
      expect(bands[i].maxZoom).toBe(bands[i + 1].minZoom);
      expect(bands[i].topRatio).toBeLessThanOrEqual(bands[i + 1].topRatio);
      expect(bands[i].sizeRatio ?? 0).toBeLessThanOrEqual(bands[i + 1].sizeRatio ?? 0);
    }
    for (const z of [0, 2.9, 3, 5.9, 6, 9.9, 10, 13.9, 14, 18, 19, 23.9]) {
      expect(activeBandIndex(z, bands)).toBeGreaterThanOrEqual(0);
    }
  });

  for (const c of corpus.cases) {
    it(`corpus/${c.id}：zoom 边界两侧同档（A03/A07）`, () => {
      const layer = layerFor(c);
      const strategy = normalizeLabelStrategy(
        layer.label as unknown as Parameters<typeof normalizeLabelStrategy>[0],
      );
      expect(strategy.zoomBands).toHaveLength(4);
      expect(bandTopRatio(c.zoom, strategy.zoomBands))
        .toBeCloseTo(c.expected.label_top_ratio as number, 6);
      if (c.id !== "pinned-size-wins") {
        const idx = activeBandIndex(c.zoom, strategy.zoomBands);
        expect(idx).toBeGreaterThanOrEqual(0);
        expect(strategy.zoomBands[idx].sizeRatio)
          .toBeCloseTo(c.expected.symbol_size_ratio as number, 6);
      }
    });
  }
});

describe("G27 有效策略与可解释交互", () => {
  const autoPolicy = { version: G27_SCALE_POLICY_VERSION, mode: "auto_lod" };

  it("A28：无消费者 / 旧项目 / 未知版本 / 非法 mode / 用户隐藏都不显示已生效", () => {
    const base = { id: "L", label: { field: "name", zoomBands: corpus.label_zoom_bands } };
    expect(isScalePortrayalApplied({ ...base, scale_profile: {} })).toBe(false);
    expect(isScalePortrayalApplied({ ...base, scale_profile: {} }, { applied: true })).toBe(false);
    expect(readScalePortrayalPolicy({ ...base, scale_profile: {} })).toBeNull();
    expect(isScalePortrayalApplied({
      ...base,
      scale_profile: { [G27_POLICY_NAMESPACE]: { ...autoPolicy, version: "old" } },
    })).toBe(false);
    expect(isScalePortrayalApplied({
      ...base,
      scale_profile: { [G27_POLICY_NAMESPACE]: { ...autoPolicy, mode: "turbo" } },
    })).toBe(false);
    expect(isScalePortrayalApplied({
      ...base,
      scale_profile: { [G27_POLICY_NAMESPACE]: { ...autoPolicy, mode: "disabled" } },
    })).toBe(false);
    expect(isScalePortrayalApplied({
      ...base,
      layout: { visibility: "none" },
      scale_profile: { [G27_POLICY_NAMESPACE]: autoPolicy },
    })).toBe(false);
    // policy 存在但 converter 没落 zoomBands：无消费者 → 不显示已生效
    expect(hasScalePortrayalConsumer({
      id: "L", scale_profile: { [G27_POLICY_NAMESPACE]: autoPolicy },
    })).toBe(false);
    expect(isScalePortrayalApplied({
      id: "L", scale_profile: { [G27_POLICY_NAMESPACE]: autoPolicy },
    })).toBe(false);
    // 非法 pin / fixed_density 缺 ratio 也不生效
    expect(isScalePortrayalApplied({
      ...base,
      scale_profile: { [G27_POLICY_NAMESPACE]: { ...autoPolicy, pinned_size_ratio: 99 } },
    })).toBe(false);
    expect(isScalePortrayalApplied({
      ...base,
      scale_profile: { [G27_POLICY_NAMESPACE]: { ...autoPolicy, mode: "fixed_density" } },
    })).toBe(false);
  });

  it("合法 policy + 真实 zoomBands → 生效；后端回执 applied=false 时以回执为准", () => {
    const layer = layerFor({
      id: "ok", zoom: 10, layer: {}, policy: autoPolicy,
      available_fields: [], expected: {},
    });
    expect(hasScalePortrayalConsumer(layer)).toBe(true);
    expect(isScalePortrayalApplied(layer)).toBe(true);
    expect(isScalePortrayalApplied(layer, { applied: false })).toBe(false);
  });

  it("A05：人工隐藏在所有 zoom 恒定，不因放大恢复", () => {
    const layer = layerFor({
      id: "H", zoom: 6,
      layer: { layout: { visibility: "none" } },
      policy: autoPolicy, available_fields: [], expected: {},
    });
    for (let z = 0; z <= 24; z += 0.5) {
      const view = resolveScalePortrayalView(layer, z);
      expect(view.visibility).toBe("none");
      expect(view.hiddenReason).toBe("SCALE.HIDDEN.USER");
      expect(view.applied).toBe(false);
    }
  });

  it("A15：DPI 不参与前端策略裁决（同 zoom 两次解析逐位一致）", () => {
    const layer = layerFor({
      id: "D", zoom: 10, layer: {}, policy: autoPolicy,
      available_fields: [], expected: {},
    });
    const a = resolveScalePortrayalView(layer, 10, { previousTier: "province" });
    const b = resolveScalePortrayalView(layer, 10, { previousTier: "province" });
    expect(a).toEqual(b);
    expect(a.visibility).toBe("visible");
    expect(a.bandId).toBe("lod-2");
  });

  it("A24：解释面有界（diagnostics ≤8，披露 ≤8），前端不自称 tier 权威", () => {
    const layer = layerFor({
      id: "B", zoom: 10, layer: {}, policy: autoPolicy,
      available_fields: [], expected: {},
    });
    const diagnostics = Array.from({ length: 20 }, (_, i) => ({
      code: `SCALE.POLICY.D${i}`, field: "mode", detail: "x".repeat(300),
    }));
    const view = resolveScalePortrayalView(layer, 10, {
      explanation: {
        applied: false, diagnostics,
        disclosures: Array.from({ length: 20 }, () => "d".repeat(300)),
        user_overrides: Array.from({ length: 20 }, () => "u".repeat(300)),
        truncated: true,
      },
    });
    expect(view.diagnostics.length).toBeLessThanOrEqual(8);
    expect(view.disclosures.length).toBeLessThanOrEqual(8);
    expect(view.userOverrides.length).toBeLessThanOrEqual(8);
    expect(view.diagnostics[0].detail.length).toBeLessThanOrEqual(160);
    expect(view.truncated).toBe(true);
    // 无后端解释面时 tier 明确标为未评估，不冒充权威
    expect(resolveScalePortrayalView(layer, 10).tier).toBe("not_evaluated");
  });
});
