import { describe, it, expect } from "vitest";
import {
  resolveRepresentationPlan,
  representationModeAtZoom,
  rawWindowsOf,
  expansionWindowsOf,
  representationSublayerId,
  MAX_WINDOWS,
  MAPLIBRE_ZOOM_MAX,
  type NormalizedRepresentationWindow,
} from "./representation";
import { resetSymbolLawEvidence } from "../map-kit/symbol-law";
import type { MapSpecLayer } from "@/lib/mapspec-compiler/types";

/**
 * 语义缩放表达窗口单一裁决器（v1.6 方向 5）的对抗性单元测试。
 *
 * 锁定契约（与后端 normalize_windows 对齐）：
 *  - 词表校验（CONTRACT_REPRESENTATION_MODES）；
 *  - min<max、钳制 [0,24]、排序、连续覆盖 [0,24)（缺口/重叠 → null）；
 *  - 相邻同模式合并 + reasonCodes 去重（≤8）；窗口数 ≤8；
 *  - 半开区间 [min,max) 的 zoom 查询 + 越界钳制；
 *  - 非法/平凡计划 → null / nonTrivial=false（消费方回退缺省，绝不猜）。
 */

/** 最小声明层（representation 是唯一被消费的键）。 */
function layerWithWindows(
  windows: Array<Partial<MapSpecLayer["representation"]>["windows"] extends (infer W)[] | undefined ? W : never> | undefined,
): Pick<MapSpecLayer, "representation"> {
  return { representation: { windows } as MapSpecLayer["representation"] };
}

/** 便捷构造：window 省略 reason_codes。 */
function w(min_zoom: number, max_zoom: number, mode: string, reason_codes?: string[]) {
  return { min_zoom, max_zoom, mode, reason_codes } as never;
}

beforeEach(() => {
  // 裁决器本身不发 evidence，但消费面共享全局环 —— 隔离防串扰。
  resetSymbolLawEvidence();
});

describe("resolveRepresentationPlan：合法计划归一化", () => {
  it("heatmap[0,11)+point_cluster[11,24) → 2 窗 + nonTrivial/hasCluster/hasHidden 标志", () => {
    const plan = resolveRepresentationPlan(
      layerWithWindows([w(0, 11, "visual_heatmap"), w(11, 24, "point_cluster")]),
    );
    expect(plan).not.toBeNull();
    expect(plan!.windows).toHaveLength(2);
    expect(plan!.windows[0]).toMatchObject({ minZoom: 0, maxZoom: 11, mode: "visual_heatmap" });
    expect(plan!.windows[1]).toMatchObject({ minZoom: 11, maxZoom: 24, mode: "point_cluster" });
    expect(plan!.nonTrivial).toBe(true);
    expect(plan!.hasCluster).toBe(true);
    expect(plan!.hasHidden).toBe(false);
  });

  it("乱序输入按 min 排序后归一化（[16,24) 在前 [0,16) 在后）", () => {
    const plan = resolveRepresentationPlan(
      layerWithWindows([w(16, 24, "point_cluster"), w(0, 16, "raw")]),
    );
    expect(plan).not.toBeNull();
    expect(plan!.windows.map((x) => [x.minZoom, x.maxZoom, x.mode])).toEqual([
      [0, 16, "raw"],
      [16, 24, "point_cluster"],
    ]);
  });

  it("窗值域外钳制 [0,24]：[-5,8)+[8,30) → [0,8)+[8,24) 合法", () => {
    const plan = resolveRepresentationPlan(
      layerWithWindows([w(-5, 8, "raw"), w(8, 30, "visual_heatmap")]),
    );
    expect(plan).not.toBeNull();
    expect(plan!.windows.map((x) => [x.minZoom, x.maxZoom])).toEqual([[0, 8], [8, 24]]);
  });

  it("reason_codes 透传且非字符串项被滤除", () => {
    const plan = resolveRepresentationPlan(
      layerWithWindows([w(0, 24, "point_cluster", ["a", 3 as never, null as never, "b"])]),
    );
    expect(plan!.windows[0].reasonCodes).toEqual(["a", "b"]);
  });

  it("artifact 模式（aggregate_grid/generalized）在词表内 → 合法计划", () => {
    const plan = resolveRepresentationPlan(
      layerWithWindows([w(0, 12, "aggregate_grid"), w(12, 24, "generalized")]),
    );
    expect(plan).not.toBeNull();
    expect(plan!.windows.map((x) => x.mode)).toEqual(["aggregate_grid", "generalized"]);
    expect(plan!.nonTrivial).toBe(true);
  });
});

describe("resolveRepresentationPlan：非法计划 → null", () => {
  it("未知 mode → null", () => {
    expect(resolveRepresentationPlan(layerWithWindows([w(0, 24, "3d_globe")]))).toBeNull();
  });

  it("min === max → null；min > max → null", () => {
    expect(
      resolveRepresentationPlan(layerWithWindows([w(0, 8, "raw"), w(8, 8, "hidden"), w(8, 24, "raw")])),
    ).toBeNull();
    expect(
      resolveRepresentationPlan(layerWithWindows([w(0, 8, "raw"), w(12, 8, "hidden"), w(8, 24, "raw")])),
    ).toBeNull();
  });

  it("缺口 [0,8)+[11,24) → null", () => {
    expect(
      resolveRepresentationPlan(layerWithWindows([w(0, 8, "raw"), w(11, 24, "raw")])),
    ).toBeNull();
  });

  it("重叠窗（[0,12)+[10,24)）→ null（连续性要求 min == 前窗 max）", () => {
    expect(
      resolveRepresentationPlan(layerWithWindows([w(0, 12, "raw"), w(10, 24, "hidden")])),
    ).toBeNull();
  });

  it("首窗 min > 0（[3,8)+[8,24)）→ null：未覆盖全域", () => {
    expect(
      resolveRepresentationPlan(layerWithWindows([w(3, 8, "raw"), w(8, 24, "raw")])),
    ).toBeNull();
  });

  it("末窗 max < 24（[0,8)+[8,20)）→ null：未覆盖全域", () => {
    expect(
      resolveRepresentationPlan(layerWithWindows([w(0, 8, "raw"), w(8, 20, "raw")])),
    ).toBeNull();
  });

  it("9 窗（> MAX_WINDOWS=8）→ null；恰好 8 窗合法", () => {
    const nine: never[] = [];
    const step = 24 / 9;
    for (let i = 0; i < 9; i++) {
      nine.push(w(+(i * step).toFixed(4), +((i + 1) * step).toFixed(4), i % 2 ? "hidden" : "raw"));
    }
    expect(resolveRepresentationPlan(layerWithWindows(nine))).toBeNull();

    const eight: never[] = [];
    const step8 = 24 / 8;
    for (let i = 0; i < 8; i++) {
      eight.push(w(i * step8, (i + 1) * step8, i % 2 ? "hidden" : "raw"));
    }
    const plan = resolveRepresentationPlan(layerWithWindows(eight));
    expect(plan).not.toBeNull();
    expect(plan!.windows).toHaveLength(MAX_WINDOWS);
  });

  it("非数值 min/max（字符串/NaN/Infinity/null）→ null", () => {
    expect(
      resolveRepresentationPlan(layerWithWindows([w("0" as never, 24, "raw")])),
    ).toBeNull();
    expect(
      resolveRepresentationPlan(layerWithWindows([w(Number.NaN, 24, "raw")])),
    ).toBeNull();
    expect(
      resolveRepresentationPlan(layerWithWindows([w(0, Number.POSITIVE_INFINITY, "raw")])),
    ).toBeNull();
    expect(
      resolveRepresentationPlan(layerWithWindows([w(null as never, 24, "raw")])),
    ).toBeNull();
  });

  it("非对象窗元素 → null", () => {
    expect(
      resolveRepresentationPlan(layerWithWindows([null as never])),
    ).toBeNull();
    expect(
      resolveRepresentationPlan(layerWithWindows(["raw" as never])),
    ).toBeNull();
  });

  it("representation 缺失 / windows 空数组 / 非数组 → null", () => {
    expect(resolveRepresentationPlan({})).toBeNull();
    expect(resolveRepresentationPlan({ representation: {} })).toBeNull();
    expect(
      resolveRepresentationPlan({ representation: { windows: [] } }),
    ).toBeNull();
    expect(
      resolveRepresentationPlan({ representation: { windows: "raw" as never } }),
    ).toBeNull();
  });
});

describe("resolveRepresentationPlan：同模式邻窗合并", () => {
  it("raw[0,8,code a]+raw[8,16,code a,b]+cluster[16,24] → raw[0,16)+cluster[16,24)，codes 去重合并", () => {
    const plan = resolveRepresentationPlan(
      layerWithWindows([
        w(0, 8, "raw", ["a"]),
        w(8, 16, "raw", ["a", "b"]),
        w(16, 24, "point_cluster", ["c"]),
      ]),
    );
    expect(plan!.windows).toHaveLength(2);
    expect(plan!.windows[0]).toMatchObject({ minZoom: 0, maxZoom: 16, mode: "raw" });
    expect(plan!.windows[0].reasonCodes).toEqual(["a", "b"]);
    expect(plan!.windows[1]).toMatchObject({ minZoom: 16, maxZoom: 24, mode: "point_cluster" });
    expect(plan!.windows[1].reasonCodes).toEqual(["c"]);
  });

  it("合并后 reasonCodes 封顶 8（10 个代码只留前 8）", () => {
    const codes = Array.from({ length: 10 }, (_, i) => `c${i}`);
    const plan = resolveRepresentationPlan(
      layerWithWindows([
        w(0, 12, "raw", codes.slice(0, 5)),
        w(12, 24, "raw", codes.slice(5)),
      ]),
    );
    expect(plan!.windows).toHaveLength(1);
    expect(plan!.windows[0].reasonCodes).toEqual(codes.slice(0, 8));
  });

  it("三段同模式乱序输入合并为单窗", () => {
    const plan = resolveRepresentationPlan(
      layerWithWindows([w(16, 24, "hidden", ["x"]), w(0, 8, "hidden"), w(8, 16, "hidden")]),
    );
    expect(plan!.windows).toHaveLength(1);
    expect(plan!.windows[0]).toMatchObject({ minZoom: 0, maxZoom: 24, mode: "hidden" });
    expect(plan!.windows[0].reasonCodes).toEqual(["x"]);
    expect(plan!.hasHidden).toBe(true);
  });
});

describe("resolveRepresentationPlan：平凡计划", () => {
  it("全 raw 计划（单窗 [0,24)）→ nonTrivial=false（hasCluster/hasHidden 均 false）", () => {
    const plan = resolveRepresentationPlan(layerWithWindows([w(0, 24, "raw")]));
    expect(plan).not.toBeNull();
    expect(plan!.nonTrivial).toBe(false);
    expect(plan!.hasCluster).toBe(false);
    expect(plan!.hasHidden).toBe(false);
  });

  it("分段 raw 合并后仍是平凡计划 → nonTrivial=false", () => {
    const plan = resolveRepresentationPlan(
      layerWithWindows([w(0, 12, "raw"), w(12, 24, "raw")]),
    );
    expect(plan!.windows).toHaveLength(1);
    expect(plan!.nonTrivial).toBe(false);
  });
});

describe("representationModeAtZoom：半开区间边界", () => {
  // plan: heatmap [0,11) / cluster [11,24)
  const plan = resolveRepresentationPlan(
    layerWithWindows([w(0, 11, "visual_heatmap"), w(11, 24, "point_cluster")]),
  )!;

  it.each([
    [0, "visual_heatmap"],
    [8, "visual_heatmap"],
    [10.999, "visual_heatmap"],
    [11, "point_cluster"], // 半开：11 属于后窗
    [23.999, "point_cluster"],
    [24, "point_cluster"], // 域外钳到 24-ε → 末窗
  ])("zoom=%s → %s", (zoom, mode) => {
    expect(representationModeAtZoom(plan, zoom as number)).toBe(mode);
  });

  it("zoom 域外钳制：-5 → 首窗；30 → 末窗", () => {
    expect(representationModeAtZoom(plan, -5)).toBe("visual_heatmap");
    expect(representationModeAtZoom(plan, 30)).toBe("point_cluster");
  });

  it("退化极小值：Number.EPSILON 级间隙内不误判窗归属", () => {
    // [0,11)+[11,24)：10.999999999999 与 11 的判读仍按半开语义落位
    expect(representationModeAtZoom(plan, 11 - 1e-12)).toBe("visual_heatmap");
    expect(representationModeAtZoom(plan, 11 + 1e-12)).toBe("point_cluster");
  });

  it("hidden 计划的 zoom 查询", () => {
    const hiddenPlan = resolveRepresentationPlan(
      layerWithWindows([w(0, 8, "hidden"), w(8, 24, "raw")]),
    )!;
    expect(representationModeAtZoom(hiddenPlan, 7.999)).toBe("hidden");
    expect(representationModeAtZoom(hiddenPlan, 8)).toBe("raw");
  });
});

describe("rawWindowsOf / expansionWindowsOf / representationSublayerId", () => {
  it("rawWindowsOf 返回按序全部 raw 窗（首元素 = 主层门）", () => {
    const plan = resolveRepresentationPlan(
      layerWithWindows([
        w(0, 11, "raw"),
        w(11, 14, "point_cluster"),
        w(14, 24, "raw"),
      ]),
    )!;
    const raws = rawWindowsOf(plan);
    expect(raws.map((x) => [x.minZoom, x.maxZoom])).toEqual([[0, 11], [14, 24]]);
  });

  it("expansionWindowsOf 排除首个 raw 窗，index 保留原窗序", () => {
    const plan = resolveRepresentationPlan(
      layerWithWindows([
        w(0, 11, "raw"),
        w(11, 14, "point_cluster"),
        w(14, 18, "visual_heatmap"),
        w(18, 24, "raw"),
      ]),
    )!;
    const exp = expansionWindowsOf(plan);
    // 首窗 raw（index 0）被排除；其余三窗各产出一条展开指令
    expect(exp.map((x) => [x.index, x.mode, x.minZoom, x.maxZoom])).toEqual([
      [1, "point_cluster", 11, 14],
      [2, "visual_heatmap", 14, 18],
      [3, "raw", 18, 24],
    ]);
  });

  it("无 raw 窗的计划：全部窗均为展开窗（主层走身份锚）", () => {
    const plan = resolveRepresentationPlan(
      layerWithWindows([w(0, 11, "visual_heatmap"), w(11, 24, "point_cluster")]),
    )!;
    expect(rawWindowsOf(plan)).toHaveLength(0);
    expect(expansionWindowsOf(plan).map((x) => x.index)).toEqual([0, 1]);
  });

  it("representationSublayerId 命名约定：${layerId}__rep${i}", () => {
    expect(representationSublayerId("L", 0)).toBe("L__rep0");
    expect(representationSublayerId("poi-dots", 3)).toBe("poi-dots__rep3");
  });
});

describe("类型面（编译期契约）", () => {
  it("NormalizedRepresentationWindow 字段齐备（编译期结构锁定）", () => {
    const win: NormalizedRepresentationWindow = {
      minZoom: 0,
      maxZoom: 24,
      mode: "raw",
      reasonCodes: [],
    };
    expect(win.maxZoom).toBe(MAPLIBRE_ZOOM_MAX);
  });
});
