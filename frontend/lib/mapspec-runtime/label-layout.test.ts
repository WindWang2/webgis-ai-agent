/**
 * label-layout 纯函数测试（ac-05，ADR-0154）。
 * 门禁对应：避让降级四级触发、zoom 分级 4 档、抽稀确定性、样式自适应。
 */
import { describe, it, expect } from "vitest";
import {
  activeBandIndex,
  averageLabelChars,
  basemapLuminance,
  buildSortKeyExpr,
  buildTextSizeExpr,
  estimateInkRatio,
  hasCJKSample,
  labelOnlyLayerChange,
  normalizeLabelStrategy,
  parseColorLuminance,
  resolveDegrade,
  resolveLabelStyle,
  selectTopLabels,
  type FeatureSample,
  type LabelStrategySpec,
} from "./label-layout";

function fc(n: number, opts: { idKey?: string; priority?: (i: number) => number; name?: (i: number) => string } = {}): FeatureSample[] {
  const out: FeatureSample[] = [];
  for (let i = 0; i < n; i++) {
    const props: Record<string, unknown> = { name: opts.name ? opts.name(i) : `poi-${i}` };
    if (opts.idKey) props[opts.idKey] = 1000 + i;
    props.pop = opts.priority ? opts.priority(i) : n - i; // 缺省：数据序即优先级降序
    out.push({ properties: props });
  }
  return out;
}

const baseStrategy: LabelStrategySpec = {
  mode: "all", topN: null, priorityField: null, zoomBands: [],
  sizeRatio: 1.0, haloMode: "static",
};

describe("normalizeLabelStrategy", () => {
  it("defaults to all/static with no declared fields (backward compat)", () => {
    const s = normalizeLabelStrategy({ field: "name" });
    expect(s).toEqual({ mode: "all", topN: null, priorityField: null, zoomBands: [], sizeRatio: 1.0, haloMode: "static" });
  });

  it("reads canonical camelCase and tolerates snake_case aliases", () => {
    const camel = normalizeLabelStrategy({ field: "n", mode: "top_n", topN: 400, priorityField: "pop", sizeRatio: 0.9, haloMode: "auto" } as any);
    const snake = normalizeLabelStrategy({ field: "n", mode: "top_n", top_n: 400, priority_field: "pop", size_ratio: 0.9, halo_mode: "auto" } as any);
    expect(camel).toEqual(snake);
    expect(camel.mode).toBe("top_n");
    expect(camel.topN).toBe(400);
    expect(camel.haloMode).toBe("auto");
  });

  it("normalizes and sorts zoom bands, dropping malformed ones", () => {
    const s = normalizeLabelStrategy({
      field: "n",
      zoomBands: [
        { minZoom: 8, maxZoom: 11, topRatio: 0.25 },
        { minZoom: 0, maxZoom: 8, topRatio: 0.1 },
        { minZoom: 5, maxZoom: 2 }, // 非法：max <= min
      ],
    } as any);
    expect(s.zoomBands).toHaveLength(2);
    expect(s.zoomBands[0].minZoom).toBe(0);
    expect(s.zoomBands[0].topRatio).toBe(0.1);
  });
});

describe("zoom bands (P4)", () => {
  const bands = normalizeLabelStrategy({
    field: "n",
    zoomBands: [
      { minZoom: 0, maxZoom: 8, topRatio: 0.1, sizeRatio: 0.9 },
      { minZoom: 8, maxZoom: 11, topRatio: 0.25, sizeRatio: 0.95 },
      { minZoom: 11, maxZoom: 14, topRatio: 0.6 },
      { minZoom: 14, maxZoom: 24, topRatio: 1.0 },
    ],
  } as any).zoomBands;

  it("maps 4 declared zoom tiers to the right band", () => {
    expect(activeBandIndex(3, bands)).toBe(0);
    expect(activeBandIndex(9, bands)).toBe(1);
    expect(activeBandIndex(12, bands)).toBe(2);
    expect(activeBandIndex(18, bands)).toBe(3);
    expect(activeBandIndex(-1, bands)).toBe(-1);
    expect(bandTopRatioAt(3)).toBe(0.1);
    expect(bandTopRatioAt(18)).toBe(1.0);
  });

  function bandTopRatioAt(zoom: number): number {
    const i = activeBandIndex(zoom, bands);
    return i >= 0 ? bands[i].topRatio : 1.0;
  }

  it("builds a step expression over bands with sizeRatio", () => {
    const expr = buildTextSizeExpr(12, bands);
    expect(Array.isArray(expr)).toBe(true);
    // step: <8 → 12*0.9; 8–11 → 12*0.95; >=11 → 12 (ratio 1/undefined)
    expect((expr as unknown[])[0]).toBe("step");
    expect((expr as unknown[])[2]).toBeCloseTo(10.8);
  });

  it("returns a scalar when no band carries sizeRatio", () => {
    expect(buildTextSizeExpr(12, [])).toBe(12);
  });
});

describe("selectTopLabels (P3 thinning)", () => {
  it("keeps everything when mode=all and no bands", () => {
    const sel = selectTopLabels(fc(500), baseStrategy, 10);
    expect(sel.method).toBe("skipped");
    expect(sel.reason).toBe("no_thinning_needed");
  });

  it("id_list path: exact top-N by priority, stable ties", () => {
    const features = fc(1000, { idKey: "id" });
    const sel = selectTopLabels(features, { ...baseStrategy, mode: "top_n", topN: 100, priorityField: "pop" }, 10);
    expect(sel.method).toBe("id_list");
    expect(sel.kept).toBe(100);
    const ids = (sel.filter![2] as unknown[])[1] as unknown[];
    expect(ids.length).toBe(100);
    // 优先级降序：pop = 1000-i → 前 100 名是 i=0..99
    expect(ids).toContain(1000);
    expect(ids).not.toContain(999); // i=100 的 id=1100 不在
  });

  it("value_cutoff path when no usable id key", () => {
    const features = fc(100).map((f) => ({
      properties: { name: f.properties!.name, pop: f.properties!.pop },
    }));
    const sel = selectTopLabels(features, { ...baseStrategy, mode: "top_n", topN: 10, priorityField: "pop" }, 10);
    expect(sel.method).toBe("value_cutoff");
    expect(sel.kept).toBeGreaterThanOrEqual(10);
    expect((sel.filter![0])).toBe(">=");
  });

  it("skips safely without a priority field", () => {
    const features = fc(1000, { idKey: "id" }).map((f) => ({ properties: { name: f.properties!.name } }));
    const sel = selectTopLabels(features, { ...baseStrategy, mode: "top_n", topN: 100 }, 10);
    expect(sel.method).toBe("skipped");
    expect(sel.reason).toBe("no_priority_field");
    expect(sel.filter).toBeNull();
  });

  it("hover_only never persists labels", () => {
    const sel = selectTopLabels(fc(10), { ...baseStrategy, mode: "hover_only" }, 10);
    expect(sel.kept).toBe(0);
    expect(sel.reason).toBe("hover_only");
  });

  it("band topRatio scales the effective limit", () => {
    const bands = [
      { minZoom: 0, maxZoom: 8, topRatio: 0.1, sizeRatio: null },
      { minZoom: 8, maxZoom: 24, topRatio: 1.0, sizeRatio: null },
    ];
    const features = fc(3200, { idKey: "id" });
    const strategy: LabelStrategySpec = { ...baseStrategy, mode: "top_n", topN: 400, priorityField: "pop", zoomBands: bands };
    expect(selectTopLabels(features, strategy, 6).kept).toBe(40);
    expect(selectTopLabels(features, strategy, 10).kept).toBe(400);
  });
});

describe("degrade ladder (P3, §0.4 four levels)", () => {
  it("level 0 below warn threshold", () => {
    const d = resolveDegrade(0.05);
    expect(d.level).toBe(0);
    expect(d.actions).toEqual([]);
  });

  it("level 1 shrinks font", () => {
    const d = resolveDegrade(0.15);
    expect(d.level).toBe(1);
    expect(d.actions).toContain("font_shrunk");
    expect(d.sizeFactor).toBeLessThan(1);
    expect(d.haloFactor).toBe(1);
  });

  it("level 2 additionally removes halo", () => {
    const d = resolveDegrade(0.3);
    expect(d.level).toBe(2);
    expect(d.actions).toEqual(["font_shrunk", "halo_removed"]);
    expect(d.haloFactor).toBe(0);
  });

  it("level 3 intensifies thinning", () => {
    const d = resolveDegrade(0.8);
    expect(d.level).toBe(3);
    expect(d.actions).toContain("thin_intensified");
    expect(d.thinFactor).toBe(0.5);
  });

  it("level 4 disables the layer's labels", () => {
    const d = resolveDegrade(2.0);
    expect(d.level).toBe(4);
    expect(d.disable).toBe(true);
  });

  it("ink ratio estimate matches the backend collision_est口径", () => {
    // 3200 × 8.4em × 12px² / (1024×768) ≈ 5.0
    const r = estimateInkRatio(3200, 8.4, 12, 3200);
    expect(r).toBeGreaterThan(4.9);
    expect(r).toBeLessThan(5.2);
  });
});

describe("adaptive style (P5)", () => {
  it("explicit color/halo always win (#1007 default on light basemap)", () => {
    const s = resolveLabelStyle({ field: "n" } as any, baseStrategy, { luminance: 0.9, cjk: false, degrade: resolveDegrade(0) });
    expect(s.textColor).toBe("#000000");
    expect(s.haloColor).toBe("#ffffff");
    expect(s.haloWidth).toBe(1);
  });

  it("auto mode flips text/halo on a dark basemap and deepens the halo", () => {
    const strategy = { ...baseStrategy, haloMode: "auto" as const };
    const light = resolveLabelStyle({ field: "n" } as any, strategy, { luminance: 0.9, cjk: false, degrade: resolveDegrade(0) });
    const dark = resolveLabelStyle({ field: "n" } as any, strategy, { luminance: 0.1, cjk: false, degrade: resolveDegrade(0) });
    expect(dark.darkBasemap).toBe(true);
    expect(dark.textColor).toBe("#f5f7fa");
    expect(dark.haloColor).toBe("#101418");
    // 晕宽随底图亮度：深底 +0.25
    expect(dark.haloWidth).toBe(1.25);
    expect(light.haloWidth).toBe(1);
  });

  it("CJK narrows max-width and enlarges halo without touching font size", () => {
    const latin = resolveLabelStyle({ field: "n" } as any, baseStrategy, { luminance: 1, cjk: false, degrade: resolveDegrade(0) });
    const cjk = resolveLabelStyle({ field: "n" } as any, baseStrategy, { luminance: 1, cjk: true, degrade: resolveDegrade(0) });
    expect(cjk.textMaxWidth).toBeLessThan(latin.textMaxWidth);
    expect(cjk.haloWidth).toBeGreaterThan(latin.haloWidth);
  });

  it("degrade level 2 zeroes the halo width", () => {
    const s = resolveLabelStyle({ field: "n" } as any, baseStrategy, { luminance: 1, cjk: false, degrade: resolveDegrade(0.3) });
    expect(s.haloWidth).toBe(0);
  });

  it("luminance parses hex and rgb and finds background layer", () => {
    expect(parseColorLuminance("#ffffff")).toBeCloseTo(1);
    expect(parseColorLuminance("#000000")).toBeCloseTo(0);
    expect(parseColorLuminance("rgb(20, 30, 40)")).toBeLessThan(0.2);
    expect(parseColorLuminance("nope")).toBe(1);
    const lum = basemapLuminance({ layers: [{ type: "background", paint: { "background-color": "#0f172a" } }] });
    expect(lum).toBeLessThan(0.45);
    expect(basemapLuminance(null)).toBe(1);
  });
});

describe("text metrics", () => {
  it("averageLabelChars uses CJK 1.0em / latin 0.6em (backend parity)", () => {
    const feats = [{ properties: { n: "城市AB" } }]; // 2×1.0 + 2×0.6 = 3.2
    expect(averageLabelChars(feats, "n")).toBeCloseTo(3.2);
  });

  it("hasCJKSample detects CJK values", () => {
    expect(hasCJKSample([{ properties: { n: "成都市" } }], "n")).toBe(true);
    expect(hasCJKSample([{ properties: { n: "Chengdu" } }], "n")).toBe(false);
  });

  it("sort key is a descending MapLibre expression", () => {
    expect(buildSortKeyExpr("pop")[0]).toBe("*");
    expect(buildSortKeyExpr("pop")[1]).toBe(-1);
  });
});

describe("label-only change detection", () => {
  const main = { id: "L", source: "s", type: "circle" as const, paint: { color: "#fff" } };
  it("true when only the label declaration differs", () => {
    const a = { ...main, label: { field: "name" } } as any;
    const b = { ...main, label: { field: "title" } } as any;
    expect(labelOnlyLayerChange(a, b)).toBe(true);
  });

  it("true when label is added to a plain layer", () => {
    expect(labelOnlyLayerChange(main as any, { ...main, label: { field: "name" } } as any)).toBe(true);
  });

  it("false when paint/layout/filter also changed", () => {
    const a = { ...main, label: { field: "name" } } as any;
    const b = { ...main, paint: { color: "#000" }, label: { field: "title" } } as any;
    expect(labelOnlyLayerChange(a, b)).toBe(false);
  });

  it("false when label declaration is identical", () => {
    const a = { ...main, label: { field: "name" } } as any;
    expect(labelOnlyLayerChange(a, { ...a })).toBe(false);
  });
});
