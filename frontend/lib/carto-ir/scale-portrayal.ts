/**
 * G27 —— 尺度表达策略的**前端只读投影**（不是第二套 resolver）。
 *
 * 权威链：后端 `app/lib/cartography/scale_portrayal.py` 复用 scene_lod /
 * scale_rules / representation 产出 `EffectiveScalePortrayal`；其 spec 投影
 * 只写既有字段 `layer.label.zoomBands`；本模块只做三件事：
 *  1. 从 `layer.scale_profile.g27_scale_portrayal` 读出 opt-in policy；
 *  2. 复用既有 `resolveLayerVisibility` / `normalizeLabelStrategy` 裁决；
 *  3. 产出有界的 UI view（当前档位 / 隐藏原因 / 是否真实生效 / 披露）。
 *
 * 纪律：
 * - 不新建第二套 MapSpec 文档、不复制阈值规则、不把 observed camera
 *   写成 desired state；zoom 变化只调用本纯函数，不产生 mutation。
 * - 没有后端 explanation 时，tier 显示 `not_evaluated` —— 前端不冒充权威。
 * - `applied=false` 时 UI 绝不显示「已生效」（A28）：
 *   旧项目 / 未知 schema / 无效 policy / 用户隐藏 / 无消费者的设置。
 */
import { resolveLayerVisibility } from "./visibility";
import {
  activeBandIndex,
  bandTopRatio,
  normalizeLabelStrategy,
} from "@/lib/mapspec-runtime/label-layout";

/** 与后端 SCALE_POLICY_VERSION 逐字一致（corpus 漂移闸锁定）。 */
export const G27_SCALE_POLICY_VERSION = "g27-scale-portrayal/1.0.0" as const;

/** 与后端 SCALE_POLICY_MODES 逐字一致。 */
export const G27_SCALE_POLICY_MODES = ["auto_lod", "fixed_density", "disabled"] as const;
export type G27ScalePolicyMode = (typeof G27_SCALE_POLICY_MODES)[number];

/** 与后端 SCALE_POLICY_UNITS 逐字一致。 */
export const G27_SCALE_POLICY_UNITS = ["zoom_level", "screen_px", "ground_m"] as const;

/** policy 的持久化命名空间（既有开放 dict `layer.scale_profile` 内）。 */
export const G27_POLICY_NAMESPACE = "g27_scale_portrayal" as const;

/**
 * 4 档 LOD 投影（**数据镜像，不是规则**）——逐值等于后端
 * `scene_lod.build_label_zoom_bands()`；由共享 corpus 双向漂移闸锁定。
 * 前端运行时真正消费的是 spec 的 `layer.label.zoomBands`，本常量仅供
 * UI 预览与契约测试，绝不作为第二权威参与渲染裁决。
 */
export const G27_LABEL_ZOOM_BANDS = [
  { minZoom: 0.0, maxZoom: 6.0, topRatio: 0.3, sizeRatio: 0.8 },
  { minZoom: 6.0, maxZoom: 10.0, topRatio: 0.6, sizeRatio: 0.9 },
  { minZoom: 10.0, maxZoom: 14.0, topRatio: 0.85, sizeRatio: 1.0 },
  { minZoom: 14.0, maxZoom: 24.0, topRatio: 1.0, sizeRatio: 1.1 },
] as const;

/** 与后端四条固定 disclosures 同语义（A11/A12/A13/A14/A15）。 */
export const G27_SCALE_DISCLOSURES = [
  "小尺度省略标注/符号是显示策略（display-only），不写回 source、分析结果或统计 count",
  "radius_px 是屏幕像素，bandwidth_m 是真实地面距离（米），跨 zoom 不保证同量纲、不伪称可直接比较",
  "输出 DPI 只影响瓦片采样与物理尺寸/字号缩放，不等价于语义 zoom，可见性裁决恒取语义 zoom",
  "decimate_budget 与 data-plane 传输 LOD 均为 display-only，非统计样本，不改变数据总量或统计 count",
] as const;

export interface ScalePortrayalDiagnostic {
  code: string;
  field: string;
  detail: string;
}

export interface ScalePortrayalView {
  /** 策略是否真实生效 —— false 时 UI 不得显示「已生效」。 */
  applied: boolean;
  policyVersion: string;
  mode: G27ScalePolicyMode | "none";
  /** 后端权威档带名；无 explanation 时 `not_evaluated`（前端不自称权威）。 */
  tier: string;
  bandId: string;
  bandIndex: number;
  effectiveZoom: number;
  visibility: "visible" | "none";
  hiddenReason: string;
  gate: { minzoom?: number; maxzoom?: number };
  topRatio: number;
  sizeRatio: number;
  userOverrides: string[];
  diagnostics: ScalePortrayalDiagnostic[];
  disclosures: string[];
  /** 解释来源：后端权威解释 / 本地既有裁决投影 / 未启用。 */
  source: "effective_portrayal" | "local_projection" | "disabled";
  truncated: boolean;
}

function asRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function strArray(value: unknown, limit: number): string[] {
  if (!Array.isArray(value)) return [];
  return value.filter((v): v is string => typeof v === "string").slice(0, limit);
}

function diagnosticsOf(value: unknown): ScalePortrayalDiagnostic[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 8).map((d) => {
    const row = asRecord(d);
    return {
      code: typeof row.code === "string" ? row.code.slice(0, 160) : "",
      field: typeof row.field === "string" ? row.field.slice(0, 160) : "",
      detail: typeof row.detail === "string" ? row.detail.slice(0, 160) : "",
    };
  });
}

/**
 * 读取 opt-in policy。旧项目（无 scale_profile / 无命名空间）与未知形状
 * → null（fail-safe），绝不把「没设置」当成「已应用」。
 */
export function readScalePortrayalPolicy(layer: unknown): Record<string, unknown> | null {
  const profile = asRecord(asRecord(layer).scale_profile);
  const policy = profile[G27_POLICY_NAMESPACE];
  if (!policy || typeof policy !== "object" || Array.isArray(policy)) return null;
  return policy as Record<string, unknown>;
}

/**
 * A28：设置必须真的被生产消费者接住（converter 已把 4 档投影写入
 * `layer.label.zoomBands`），否则不能声称「已生效」。
 */
export function hasScalePortrayalConsumer(layer: unknown): boolean {
  const label = asRecord(asRecord(layer).label);
  const raw = label.zoomBands ?? label.zoom_bands;
  return Array.isArray(raw) && raw.length > 0;
}

function finiteInRange(value: unknown, lo: number, hi: number): boolean {
  return typeof value === "number" && Number.isFinite(value) && value >= lo && value <= hi;
}

/** A28：无消费者 / 无效 / 用户隐藏 / 后端未确认的设置不得显示「已生效」。 */
export function isScalePortrayalApplied(
  layer: unknown,
  explanation?: Record<string, unknown> | null,
): boolean {
  const policy = readScalePortrayalPolicy(layer);
  if (policy === null) return false;
  if (policy.version !== G27_SCALE_POLICY_VERSION) return false;
  if (!G27_SCALE_POLICY_MODES.includes(policy.mode as G27ScalePolicyMode)) return false;
  if (policy.mode === "disabled") return false;
  if (policy.unit !== undefined && !G27_SCALE_POLICY_UNITS.includes(policy.unit as never)) return false;
  if (policy.pinned_size_ratio !== undefined && policy.pinned_size_ratio !== null
    && !finiteInRange(policy.pinned_size_ratio, 0.1, 4.0)) return false;
  if (policy.pinned_label_top_ratio !== undefined && policy.pinned_label_top_ratio !== null
    && !finiteInRange(policy.pinned_label_top_ratio, 0.0, 1.0)) return false;
  if (policy.mode === "fixed_density"
    && !finiteInRange(policy.pinned_label_top_ratio, 0.0, 1.0)) return false;
  const vis = resolveLayerVisibility(
    layer as unknown as Parameters<typeof resolveLayerVisibility>[0],
  );
  if (vis.visibility === "none") return false;
  if (!hasScalePortrayalConsumer(layer)) return false;
  // 后端解释面在场时以它为准（effective policy 是唯一权威）。
  if (explanation) return explanation.applied === true;
  return true;
}

/**
 * 单一 UI 解析入口。优先消费后端 `bounded_explanation`；无解释面时退化为
 * 既有 visibility / label 裁决的**只读投影**（不新增任何语义）。
 */
export function resolveScalePortrayalView(
  layer: unknown,
  zoom: number,
  options: {
    explanation?: Record<string, unknown> | null;
    previousTier?: string;
  } = {},
): ScalePortrayalView {
  const row = asRecord(layer);
  const policy = readScalePortrayalPolicy(layer);
  const explanation = options.explanation ?? null;
  const z = Number.isFinite(zoom) ? Math.min(24, Math.max(0, zoom)) : 0;

  const vis = resolveLayerVisibility(
    layer as unknown as Parameters<typeof resolveLayerVisibility>[0],
  );
  const strategy = normalizeLabelStrategy(
    row.label as unknown as Parameters<typeof normalizeLabelStrategy>[0],
  );
  const bandIndex = activeBandIndex(z, strategy.zoomBands);
  const topRatio = bandTopRatio(z, strategy.zoomBands);
  const sizeRatio = bandIndex >= 0
    ? (strategy.zoomBands[bandIndex].sizeRatio ?? strategy.sizeRatio)
    : strategy.sizeRatio;

  const applied = isScalePortrayalApplied(layer, explanation);
  const mode = policy
    ? (G27_SCALE_POLICY_MODES.includes(policy.mode as G27ScalePolicyMode)
      ? policy.mode as G27ScalePolicyMode : "disabled")
    : "none";

  // 隐藏原因：用户 durable 隐藏恒优先，其次结构 zoom 门；与后端口径一致。
  let hiddenReason = "";
  if (vis.visibility === "none") hiddenReason = "SCALE.HIDDEN.USER";
  else if (
    (vis.gate.minzoom !== undefined && z < vis.gate.minzoom)
    || (vis.gate.maxzoom !== undefined && z >= vis.gate.maxzoom)
  ) hiddenReason = "SCALE.GATED.ZOOM";
  if (explanation && typeof explanation.hidden_reason === "string" && explanation.hidden_reason) {
    hiddenReason = explanation.hidden_reason;
  }

  const expDiagnostics = diagnosticsOf(explanation?.diagnostics);
  const expDisclosures = strArray(explanation?.disclosures, 8);

  return {
    applied,
    policyVersion: policy && typeof policy.version === "string"
      ? policy.version : G27_SCALE_POLICY_VERSION,
    mode,
    tier: explanation && typeof explanation.tier === "string"
      ? explanation.tier : "not_evaluated",
    bandId: explanation && typeof explanation.band_id === "string"
      ? explanation.band_id : (bandIndex >= 0 ? `lod-${bandIndex}` : "disabled"),
    bandIndex,
    effectiveZoom: z,
    visibility: vis.visibility,
    hiddenReason,
    gate: { minzoom: vis.gate.minzoom, maxzoom: vis.gate.maxzoom },
    topRatio,
    sizeRatio,
    userOverrides: strArray(explanation?.user_overrides, 8),
    diagnostics: expDiagnostics,
    disclosures: expDisclosures.length > 0 ? expDisclosures : [...G27_SCALE_DISCLOSURES],
    source: explanation ? "effective_portrayal" : (applied ? "local_projection" : "disabled"),
    truncated: explanation?.truncated === true,
  };
}
