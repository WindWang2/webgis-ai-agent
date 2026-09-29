import { describe, it, expect } from "vitest";
import {
  resolveVisibility,
  resolveZoomGate,
  resolveLayerVisibility,
} from "./visibility";
import type { MapSpecLayer } from "@/lib/mapspec-compiler/types";

/**
 * C11 可见性/zoom 单一裁决面测试。
 *
 * 锁定 precedence：
 *  - 显隐：layout.visibility（用户 durable）恒最优先 > authored
 *    visible:false 作初值 > 默认 "visible"；
 *  - zoom：visibility.min_zoom/max_zoom 结构门 + street_detail_minzoom
 *    （唯一 gating hint，参与 max 合成下限）；admin_boundary_detail 是
 *    non_gating_hint（只披露不门控）；未知键 unknown_hint；
 *  - 病态门 min>=max → 双清空 + degenerate_gate 披露；zoom clamp [0,24]。
 */

/** 构造最小 MapSpecLayer（只关心 visible/layout/visibility 三键）。 */
function layerWith(
  overrides: Partial<Pick<MapSpecLayer, "visible" | "layout" | "visibility">>,
): Pick<MapSpecLayer, "visible" | "layout" | "visibility"> {
  return { ...overrides };
}

describe("resolveVisibility：显隐 precedence", () => {
  it("无任何信号 → visible、fromAuthoredVisible=false", () => {
    const r = resolveVisibility(layerWith({}));
    expect(r.visibility).toBe("visible");
    expect(r.fromAuthoredVisible).toBe(false);
  });

  it('layout.visibility="none" → none（用户 durable 决策终裁）', () => {
    const r = resolveVisibility(layerWith({ layout: { visibility: "none" } }));
    expect(r.visibility).toBe("none");
    expect(r.fromAuthoredVisible).toBe(false);
  });

  it("visible===false（authored）→ none、fromAuthoredVisible=true", () => {
    const r = resolveVisibility(layerWith({ visible: false }));
    expect(r.visibility).toBe("none");
    expect(r.fromAuthoredVisible).toBe(true);
  });

  it('layout.visibility="none" 覆盖 visible===false（显式胜、非 authored 推出）', () => {
    const r = resolveVisibility(
      layerWith({ visible: false, layout: { visibility: "none" } }),
    );
    expect(r.visibility).toBe("none");
    expect(r.fromAuthoredVisible).toBe(false);
  });

  it('layout.visibility="visible" 覆盖 visible===false（显式 visible 赢）', () => {
    const r = resolveVisibility(
      layerWith({ visible: false, layout: { visibility: "visible" } }),
    );
    expect(r.visibility).toBe("visible");
    expect(r.fromAuthoredVisible).toBe(false);
  });

  it("visible:true 但无显式 layout.visibility → visible 且非 authored 推出", () => {
    const r = resolveVisibility(layerWith({ visible: true }));
    expect(r.visibility).toBe("visible");
    expect(r.fromAuthoredVisible).toBe(false);
  });

  it("layout 存在但无 visibility 键 → 回落 authored/default 通道", () => {
    const r = resolveVisibility(layerWith({ visible: false, layout: { labelField: "name" } }));
    expect(r.visibility).toBe("none");
    expect(r.fromAuthoredVisible).toBe(true);
  });
});

describe("resolveZoomGate：结构门与提示词表", () => {
  it("无 visibility 结构 → 空 gate、空 ignoredHints", () => {
    const r = resolveZoomGate(layerWith({}));
    expect(r.gate.minzoom).toBeUndefined();
    expect(r.gate.maxzoom).toBeUndefined();
    expect(r.ignoredHints).toEqual([]);
  });

  it("min_zoom=8.5 / max_zoom=14 → gate 精确透传", () => {
    const r = resolveZoomGate(
      layerWith({ visibility: { min_zoom: 8.5, max_zoom: 14 } }),
    );
    expect(r.gate).toEqual({ minzoom: 8.5, maxzoom: 14 });
    expect(r.ignoredHints).toEqual([]);
  });

  it("只设 min_zoom → maxzoom 不设门", () => {
    const r = resolveZoomGate(layerWith({ visibility: { min_zoom: 5 } }));
    expect(r.gate.minzoom).toBe(5);
    expect(r.gate.maxzoom).toBeUndefined();
  });

  it("gating hint street_detail_minzoom 单独出现 → 合成下限 11", () => {
    const r = resolveZoomGate(
      layerWith({ visibility: { hints: { street_detail_minzoom: 11 } } }),
    );
    expect(r.gate.minzoom).toBe(11);
    expect(r.gate.maxzoom).toBeUndefined();
    expect(r.ignoredHints).toEqual([]);
  });

  it("street_detail_minzoom 与显式 min_zoom=8 同时 → max(8,11)=11", () => {
    const r = resolveZoomGate(
      layerWith({
        visibility: { min_zoom: 8, hints: { street_detail_minzoom: 11 } },
      }),
    );
    expect(r.gate.minzoom).toBe(11);
    expect(r.ignoredHints).toEqual([]);
  });

  it("admin_boundary_detail 是 non_gating_hint → 只披露、不进门控", () => {
    const r = resolveZoomGate(
      layerWith({ visibility: { hints: { admin_boundary_detail: 11 } } }),
    );
    expect(r.gate.minzoom).toBeUndefined();
    expect(r.ignoredHints).toHaveLength(1);
    expect(r.ignoredHints[0]?.key).toBe("admin_boundary_detail");
    expect(r.ignoredHints[0]?.value).toBe(11);
    expect(r.ignoredHints[0]?.reason).toBe("non_gating_hint");
  });

  it("未知提示键 → unknown_hint 披露（extra=allow 纪律，不拒绝）", () => {
    const r = resolveZoomGate(
      layerWith({ visibility: { hints: { future_hint: 5 } } }),
    );
    expect(r.gate.minzoom).toBeUndefined();
    expect(r.ignoredHints).toHaveLength(1);
    expect(r.ignoredHints[0]?.key).toBe("future_hint");
    expect(r.ignoredHints[0]?.value).toBe(5);
    expect(r.ignoredHints[0]?.reason).toBe("unknown_hint");
  });

  it("病态门 min_zoom 15 >= max_zoom 10 → 双清空 + degenerate_gate 披露", () => {
    const r = resolveZoomGate(
      layerWith({ visibility: { min_zoom: 15, max_zoom: 10 } }),
    );
    expect(r.gate.minzoom).toBeUndefined();
    expect(r.gate.maxzoom).toBeUndefined();
    expect(r.ignoredHints).toHaveLength(1);
    expect(r.ignoredHints[0]?.key).toBe("visibility.min_zoom>=max_zoom");
    expect(r.ignoredHints[0]?.value).toBe(15);
    expect(r.ignoredHints[0]?.reason).toBe("degenerate_gate");
  });

  it("zoom clamp 到 MapLibre 值域 [0,24]：-3 → 0、99 → 24", () => {
    const r = resolveZoomGate(
      layerWith({ visibility: { min_zoom: -3, max_zoom: 99 } }),
    );
    expect(r.gate.minzoom).toBe(0);
    expect(r.gate.maxzoom).toBe(24);
    // 0 < 24：clamp 后不再是病态门
    expect(r.ignoredHints).toEqual([]);
  });

  it("非有限 zoom 值（NaN）→ 不设门", () => {
    const r = resolveZoomGate(
      layerWith({ visibility: { min_zoom: Number.NaN } }),
    );
    expect(r.gate.minzoom).toBeUndefined();
    expect(r.gate.maxzoom).toBeUndefined();
  });
});

describe("resolveLayerVisibility：显隐 × zoom 正交组合", () => {
  it("visible:false + visibility.min_zoom=8 → none + gate.minzoom 8", () => {
    const r = resolveLayerVisibility(
      layerWith({ visible: false, visibility: { min_zoom: 8 } }),
    );
    expect(r.visibility).toBe("none");
    expect(r.fromAuthoredVisible).toBe(true);
    expect(r.gate.minzoom).toBe(8);
    expect(r.gate.maxzoom).toBeUndefined();
    expect(r.ignoredHints).toEqual([]);
  });

  it("显式 layout.visibility + hint 披露一次算齐", () => {
    const r = resolveLayerVisibility(
      layerWith({
        layout: { visibility: "visible" },
        visibility: {
          min_zoom: 3,
          hints: { street_detail_minzoom: 12, admin_boundary_detail: 7 },
        },
      }),
    );
    expect(r.visibility).toBe("visible");
    expect(r.fromAuthoredVisible).toBe(false);
    // max(3, 12) = 12；admin_boundary_detail 进披露
    expect(r.gate.minzoom).toBe(12);
    expect(r.ignoredHints.map((h) => h.key)).toEqual(["admin_boundary_detail"]);
  });
});
