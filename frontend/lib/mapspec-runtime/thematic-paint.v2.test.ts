import { describe, expect, it, beforeEach } from "vitest";
import {
  isThematic,
  legendSpecToColorExpression,
  thematicField,
} from "./thematic-paint";
import {
  getSymbolLawEvidence,
  resetSymbolLawEvidence,
} from "@/lib/map-kit/symbol-law";
import v2Schema from "../../../docs/dev/ac-06-legend-spec-v2.schema.json";

/**
 * AC-06 (ADR-0155) P6 — legend_spec v2（ADR-0152，03 线冻结 schema）投影。
 *
 * 03 线 PR #1258 尚未合入：按其分支置顶的
 * `docs/dev/ac-03-legend-spec-v2.schema.json`（本仓快照 =
 * `docs/dev/ac-06-legend-spec-v2.schema.json`）本地 fixture 驱动。
 *
 * 契约：
 *  - `out_of_range`（clip_p99 裁剪尾）→ paint 显式映射 `out_of_range.color`；
 *  - `nodata`（v1）+ v2 `nodata_label`：nodata guard 不回退；
 *  - `unit`/`k`/`method`/`palette_id`/`why`/`context` 等 metadata → evidence
 *    披露（不静默丢弃）；
 *  - v1 产出（无新字段）表达式逐字节不变（向后兼容）。
 */

describe("AC-06 P6: legend_spec v2 投影", () => {
  beforeEach(() => resetSymbolLawEvidence());

  it("本地 schema 快照与 03 线冻结版的关键 v2 字段一致", () => {
    const props = v2Schema.properties as Record<string, any>;
    expect(props.unit).toBeDefined();
    expect(props.k).toBeDefined();
    expect(props.method).toBeDefined();
    expect(props.clip_policy).toBeDefined();
    expect(props.out_of_range).toBeDefined();
    expect(props.nodata_label).toBeDefined();
    expect(props.why).toBeDefined();
  });

  it("v1 形状（无 v2 字段）产出不变，且 nodata guard 保留", () => {
    const v1 = {
      type: "graduated",
      field: "pop",
      breaks: [0, 10, 20],
      palette_colors: ["#aaa", "#bbb", "#ccc"],
      nodata: { color: "#000000" },
    };
    expect(isThematic(v1 as any)).toBe(true);
    expect(thematicField(v1 as any)).toBe("pop");
    const expr = legendSpecToColorExpression(v1 as any) as unknown[];
    expect(expr[0]).toBe("case"); // nodata guard
    const step = expr[3] as unknown[];
    expect(step[0]).toBe("step");
    const snap = getSymbolLawEvidence();
    expect(snap.counts["legend-v2-metadata"] ?? 0).toBe(0); // 无 v2 字段 → 无披露
  });

  it("v2 out_of_range：值 > upper 显式映射裁剪色（case 包装，nodata 在外层）", () => {
    const v2 = {
      type: "graduated",
      field: "pop",
      breaks: [0, 10, 20],
      palette_colors: ["#aaa", "#bbb", "#ccc"],
      clip_policy: "clip_p99",
      nodata: { color: "#000000" },
      out_of_range: { color: "#ff00ff", label: ">P99", count: 3, upper: 18.5 },
      unit: "人/km²",
      k: 3,
      method: "quantiles",
      why: "k=3 clamped",
    };
    const expr = legendSpecToColorExpression(v2 as any) as unknown[];
    // 外层 nodata guard，内层 out_of_range guard，核心 step 不变。
    expect(expr[0]).toBe("case");
    expect(expr[1]).toEqual(["==", ["get", "pop"], null]);
    const oor = expr[3] as unknown[];
    expect(oor[0]).toBe("case");
    expect(oor[1]).toEqual([">", ["to-number", ["get", "pop"]], 18.5]);
    expect(oor[2]).toBe("#ff00ff");
    const step = oor[3] as unknown[];
    expect(step[0]).toBe("step");

    const snap = getSymbolLawEvidence();
    expect(snap.counts["legend-v2-metadata"]).toBe(1);
    const meta = snap.events.find((e) => e.kind === "legend-v2-metadata");
    const fields = (meta?.detail as { fields: string[] }).fields;
    expect(fields).toEqual(expect.arrayContaining(["unit", "k", "method", "why"]));
  });

  it("v2 out_of_range 对 continuous 同样生效", () => {
    const v2 = {
      type: "continuous",
      field: "temp",
      min: 0,
      max: 40,
      palette_colors: ["#00f", "#f00"],
      out_of_range: { color: "#333333", label: ">max", count: 1, upper: 40 },
    };
    const expr = legendSpecToColorExpression(v2 as any) as unknown[];
    expect(expr[0]).toBe("case");
    expect(expr[1]).toEqual([">", ["to-number", ["get", "temp"]], 40]);
    expect(expr[2]).toBe("#333333");
    const interp = expr[3] as unknown[];
    expect(interp[0]).toBe("interpolate");
  });

  it("categorical 上出现 out_of_range → 显式 evidence（数值域语义不适用）", () => {
    const v2 = {
      type: "categorical",
      field: "landuse",
      categories: [
        { key: "R", color: "#111" },
        { key: "C", color: "#222" },
      ],
      out_of_range: { color: "#333", label: "x", count: 1, upper: 5 },
    };
    const expr = legendSpecToColorExpression(v2 as any) as unknown[];
    expect(expr?.[0]).toBe("match");
    const snap = getSymbolLawEvidence();
    expect(snap.counts["unmapped-paint-key"]).toBeGreaterThanOrEqual(1);
  });

  it("upper/color 形状不合法时 out_of_range 被忽略但 nodata 仍生效", () => {
    const v2 = {
      type: "graduated",
      field: "pop",
      breaks: [0, 10, 20],
      palette_colors: ["#aaa", "#bbb", "#ccc"],
      nodata: { color: "#000" },
      out_of_range: { color: 42, label: "bad", count: 1, upper: "not-a-number" },
    };
    const expr = legendSpecToColorExpression(v2 as any) as unknown[];
    expect(expr?.[0]).toBe("case"); // nodata
    const step = (expr as unknown[])[3] as unknown[];
    expect(step?.[0]).toBe("step"); // 无 out_of_range 包装
  });
});
