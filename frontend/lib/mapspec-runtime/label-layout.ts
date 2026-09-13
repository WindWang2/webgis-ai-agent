/**
 * label-layout — 标注布局决策的纯函数模块（ac-05，ADR-0154）。
 *
 * 职责边界：
 *  - **不渲染**：全部函数无 IO、无随机、无时钟 —— 同输入两次调用逐位
 *    相等（与后端 label_plan / label_engine 的确定性契约同规）；
 *  - **只服务交互运行时**（runtime.ts 的标注子层路径）。导出/SVG 的
 *    确定性避让走 mapspec-compiler/label-solver.ts（ADR-0126 孪生），
 *    两者互不依赖；
 *  - **字号只给比例**：绝对基准 = spec 显式 `label.size`（06 线符号律
 *    落地后由其给基准），本模块产出 `sizeRatio` / band `sizeRatio`
 *    的乘积系数，不定义绝对字号。
 *
 * 与后端的契约：`layer.label` 的策略字段（mode/topN/priorityField/
 * zoomBands/sizeRatio/haloMode）由后端 label_plan（ADR-0154 §P1/P2）
 * 经 label_layer 组件写入；本模块是这些声明的**唯一交互侧消费者**。
 *
 * 避让模型：MapLibre 内置 `text-allow-overlap:false` 是逐帧贪心（按
 * symbol-sort-key 升序放置）——本模块给它三件它没有的东西：
 *  1. **全局优先级**：`symbol-sort-key` 表达式（重要度字段降序）；
 *  2. **确定性抽稀**：`selectTopLabels` 按优先级取 Top-N（stable tie =
 *    数据序），生成 label 子层 filter（id 列表 / 数值 cutoff 双策略）；
 *  3. **zoom 分级**：`zoomBands` 按 zoom 档收放 topRatio 与字号系数。
 *
 * 降级阶梯（§0.4）：缩字号 → 去晕圈 → 加密抽稀 → 关闭该层标注；
 * `resolveDegrade` 按注记墨量比（与后端 collision_est 同口径：CJK
 * 1.0em / 其他 0.6em，1024×768 视口）确定性选级，每级产出事件供
 * runtime evidence。
 */
import type { MapSpecLayer, MapSpecLayerLabel } from "@/lib/mapspec-compiler/types";

// ── 视口估计常量（与 semantic_checks._VIEWPORT_* 同口径）───────────────
export const VIEWPORT_WIDTH_PX = 1024.0;
export const VIEWPORT_HEIGHT_PX = 768.0;
export const LABEL_WARN_RATIO = 0.10;
export const LABEL_FAIL_RATIO = 0.25;
export const LABEL_SEVERE_RATIO = 0.50;
export const LABEL_EXTREME_RATIO = 1.50;

/** 抽稀特征 id 候选键（确定性扫描序；全部命中才用第一个）。 */
const ID_KEY_CANDIDATES = ["__fid", "id", "fid", "OBJECTID", "osm_id", "station_id"] as const;

// ── 策略归一化 ─────────────────────────────────────────────────────────
/** 归一化后的标注策略（snake/camel 双收，canonical = camel）。 */
export interface LabelStrategySpec {
  mode: "all" | "top_n" | "hover_only";
  topN: number | null;
  priorityField: string | null;
  zoomBands: LabelZoomBandSpec[];
  sizeRatio: number;
  haloMode: "auto" | "static";
}

export interface LabelZoomBandSpec {
  minZoom: number;
  maxZoom: number;
  topRatio: number;
  sizeRatio: number | null;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function normalizeBands(raw: unknown): LabelZoomBandSpec[] {
  if (!Array.isArray(raw)) return [];
  const out: LabelZoomBandSpec[] = [];
  for (const b of raw) {
    if (!b || typeof b !== "object") continue;
    const band = b as Record<string, unknown>;
    const minZoom = num(band.minZoom ?? band.min_zoom);
    const maxZoom = num(band.maxZoom ?? band.max_zoom);
    if (minZoom === null || maxZoom === null || maxZoom <= minZoom) continue;
    out.push({
      minZoom,
      maxZoom,
      topRatio: Math.min(Math.max(num(band.topRatio ?? band.top_ratio) ?? 1.0, 0.0), 1.0),
      sizeRatio: num(band.sizeRatio ?? band.size_ratio),
    });
  }
  // 按 minZoom 升序（同键取先声明者 —— stable）
  return out
    .map((b, i) => ({ b, i }))
    .sort((x, y) => x.b.minZoom - y.b.minZoom || x.i - y.i)
    .map(({ b }) => b);
}

/** 从 spec `layer.label` 读取策略声明；无声明 → 全量策略（向后兼容）。 */
export function normalizeLabelStrategy(label: MapSpecLayerLabel | undefined | null): LabelStrategySpec {
  const raw = (label ?? {}) as unknown as Record<string, unknown>;
  const modeRaw = raw.mode;
  const mode: LabelStrategySpec["mode"] =
    modeRaw === "top_n" || modeRaw === "hover_only" ? modeRaw : "all";
  const sizeRatio = num(raw.sizeRatio ?? raw.size_ratio) ?? 1.0;
  return {
    mode,
    topN: num(raw.topN ?? raw.top_n),
    priorityField:
      typeof raw.priorityField === "string" ? raw.priorityField
        : typeof raw.priority_field === "string" ? (raw.priority_field as string)
        : null,
    zoomBands: normalizeBands(raw.zoomBands ?? raw.zoom_bands),
    sizeRatio: Math.min(Math.max(sizeRatio, 0.1), 4.0),
    haloMode: raw.haloMode === "auto" || raw.halo_mode === "auto" ? "auto" : "static",
  };
}

// ── 文本度量（与后端 collision_est / label_engine 同口径）──────────────
function isCJKChar(ch: string): boolean {
  const o = ch.codePointAt(0);
  if (o === undefined) return false;
  return (
    (o >= 0x3000 && o <= 0x303f) || (o >= 0x3400 && o <= 0x4dbf) ||
    (o >= 0x4e00 && o <= 0x9fff) || (o >= 0xf900 && o <= 0xfaff) ||
    (o >= 0xff00 && o <= 0xffef)
  );
}

/** em 宽度：CJK 1.0 / 其他 0.6（后端同口径）。 */
export function labelEmWidth(text: string): number {
  let w = 0;
  for (const ch of text) w += isCJKChar(ch) ? 1.0 : 0.6;
  return w;
}

export interface FeatureSample {
  properties: Record<string, unknown> | null;
}

/** 字段样本平均 em 宽（≤64 样本，去空；无样本 → null）。 */
export function averageLabelChars(features: FeatureSample[], field: string): number | null {
  let sum = 0;
  let n = 0;
  const cap = Math.min(features.length, 64);
  for (let i = 0; i < cap; i++) {
    const v = features[i]?.properties?.[field];
    if (v === null || v === undefined) continue;
    sum += labelEmWidth(String(v));
    n += 1;
  }
  return n > 0 ? sum / n : null;
}

/** 字段样本是否含 CJK（≤64 样本）。 */
export function hasCJKSample(features: FeatureSample[], field: string): boolean {
  const cap = Math.min(features.length, 64);
  for (let i = 0; i < cap; i++) {
    const v = features[i]?.properties?.[field];
    if (typeof v === "string" && /[\u3000-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/.test(v)) {
      return true;
    }
  }
  return false;
}

/** 注记墨量比（估计）：estLabels × avgChars × fontPx² / 视口像素。 */
export function estimateInkRatio(
  featureCount: number,
  avgChars: number | null,
  fontPx: number,
  estVisible = featureCount,
): number {
  if (featureCount <= 0 || avgChars === null || fontPx <= 0) return 0;
  return (estVisible * avgChars * fontPx * fontPx) / (VIEWPORT_WIDTH_PX * VIEWPORT_HEIGHT_PX);
}

// ── zoom 分级 ──────────────────────────────────────────────────────────
/** zoom 所在档下标（区间左闭右开）；越界 → -1（= 不按档过滤）。 */
export function activeBandIndex(zoom: number, bands: LabelZoomBandSpec[]): number {
  for (let i = 0; i < bands.length; i++) {
    if (zoom >= bands[i].minZoom && zoom < bands[i].maxZoom) return i;
  }
  return -1;
}

/** 档内 topRatio（无档 / 越界 → 1.0）。 */
export function bandTopRatio(zoom: number, bands: LabelZoomBandSpec[]): number {
  const i = activeBandIndex(zoom, bands);
  return i >= 0 ? bands[i].topRatio : 1.0;
}

/**
 * 字号表达式：base × sizeRatio ×（band.sizeRatio 的 step 复合）。
 * 无 band 或全部 sizeRatio 缺省 → 标量；有档 → MapLibre step 表达式
 * （zoom 阈值取 band.maxZoom 递增，输出乘该档系数）。
 */
export function buildTextSizeExpr(
  base: number,
  bands: LabelZoomBandSpec[],
): number | unknown[] {
  const withRatio = bands.filter((b) => b.sizeRatio !== null && b.sizeRatio !== 1.0);
  if (withRatio.length === 0) return base;
  // step 语义：zoom < b1.minZoom → 首档系数；此后逐档切换；越过末档
  // maxZoom → 回到 base（末档之后的 zoom 属于未声明区间，按全量字号）。
  const flat: unknown[] = [base * withRatio[0].sizeRatio!];
  for (let i = 1; i < withRatio.length; i++) {
    flat.push(withRatio[i].minZoom, base * withRatio[i].sizeRatio!);
  }
  flat.push(withRatio[withRatio.length - 1].maxZoom, base);
  return ["step", ["zoom"], ...flat];
}

// ── 优先级 ─────────────────────────────────────────────────────────────
/** symbol-sort-key 表达式：重要度降序（大值先放）= 取负。缺字段按 0。 */
export function buildSortKeyExpr(priorityField: string): unknown[] {
  return ["*", -1, ["to-number", ["coalesce", ["get", priorityField], 0]]];
}

// ── 抽稀 ───────────────────────────────────────────────────────────────
export interface TopLabelSelection {
  /** 档内有效上限（ceil(topN × topRatio)）；mode=all → 全量。 */
  limit: number;
  kept: number;
  method: "id_list" | "value_cutoff" | "skipped";
  /** label 子层 filter（method=skipped → null）。 */
  filter: unknown[] | null;
  idKey: string | null;
  cutoffValue: number | null;
  reason: string;
}

function detectIdKey(features: FeatureSample[]): string | null {
  const cap = Math.min(features.length, 50);
  for (const key of ID_KEY_CANDIDATES) {
    let hits = 0;
    for (let i = 0; i < cap; i++) {
      const v = features[i]?.properties?.[key];
      if (v !== null && v !== undefined) hits += 1;
    }
    if (cap > 0 && hits / cap >= 0.9) return key;
  }
  return null;
}

function numericPriority(features: FeatureSample[], field: string): number[] {
  const vals: number[] = [];
  for (const f of features) {
    const v = f?.properties?.[field];
    const n = typeof v === "number" ? v : typeof v === "string" && v !== "" && Number.isFinite(Number(v)) ? Number(v) : NaN;
    vals.push(Number.isFinite(n) ? n : -Infinity);
  }
  return vals;
}

/**
 * 确定性 Top-N 抽稀：优先级降序（stable tie = 数据序），取前 limit 个。
 *
 * filter 策略：
 *  - 数据带可用 id 键（≥90% 非空）→ `["in", ["get", idKey], literal(ids)]`
 *    （精确集合，id 值数组 ≤ limit）；
 *  - 无 id 键但优先级字段数值且去重值数 > limit → 数值 cutoff
 *    `[">=", to-number(get(pf)), cutoff]`（同值并列会超选，如实记入 kept）；
 *  - 否则 skipped（无法安全抽稀 —— 宁可让 MapLibre 碰撞器吸收）。
 */
export function selectTopLabels(
  features: FeatureSample[],
  strategy: LabelStrategySpec,
  zoom: number,
): TopLabelSelection {
  const skipped: TopLabelSelection = {
    limit: features.length, kept: features.length, method: "skipped",
    filter: null, idKey: null, cutoffValue: null, reason: "",
  };
  if (strategy.mode === "hover_only") {
    return { ...skipped, limit: 0, kept: 0, reason: "hover_only" };
  }
  if (features.length === 0) return { ...skipped, reason: "no_features" };

  const ratio = bandTopRatio(zoom, strategy.zoomBands);
  let limit = features.length;
  if (strategy.mode === "top_n" && strategy.topN !== null && strategy.topN > 0) {
    limit = Math.min(features.length, Math.ceil(strategy.topN * ratio));
  } else if (strategy.zoomBands.length > 0 && ratio < 1.0) {
    limit = Math.min(features.length, Math.ceil(features.length * ratio));
  }
  if (limit >= features.length) {
    return { ...skipped, reason: "no_thinning_needed" };
  }

  const pf = strategy.priorityField;
  if (!pf) return { ...skipped, limit, reason: "no_priority_field" };
  const pri = numericPriority(features, pf);
  if (!pri.some((v) => Number.isFinite(v) && v !== -Infinity)) {
    return { ...skipped, limit, reason: "priority_not_numeric" };
  }

  // 稳定名次：(-priority, index) 升序
  const order = pri
    .map((v, i) => ({ v, i }))
    .sort((a, b) => (b.v - a.v) || (a.i - b.i));

  const idKey = detectIdKey(features);
  if (idKey) {
    const ids: unknown[] = [];
    for (let k = 0; k < limit; k++) {
      const v = features[order[k].i]?.properties?.[idKey];
      if (v !== null && v !== undefined) ids.push(v);
    }
    return {
      limit, kept: ids.length, method: "id_list",
      filter: ["in", ["get", idKey], ["literal", ids]],
      idKey, cutoffValue: null,
      reason: `top_n(id=${idKey},limit=${limit},ratio=${ratio})`,
    };
  }

  // 无 id 键：数值 cutoff（第 limit 大的值；并列超选如实入 kept）
  const distinct = new Set(pri.filter((v) => Number.isFinite(v) && v !== -Infinity));
  if (distinct.size <= limit) {
    return { ...skipped, limit, reason: "priority_ties_too_coarse" };
  }
  const cutoff = order[limit - 1].v;
  let kept = 0;
  for (const v of pri) if (v >= cutoff) kept += 1;
  return {
    limit, kept, method: "value_cutoff",
    filter: [">=", ["to-number", ["get", pf]], cutoff],
    idKey: null, cutoffValue: cutoff,
    reason: `top_n(cutoff=${cutoff},limit=${limit},kept=${kept},ratio=${ratio})`,
  };
}

// ── 降级阶梯 ───────────────────────────────────────────────────────────
export interface DegradeDecision {
  level: 0 | 1 | 2 | 3 | 4;
  inkRatio: number;
  /** 应用到字号基准的乘数。 */
  sizeFactor: number;
  /** haloWidth 乘数（L2 起去晕圈）。 */
  haloFactor: number;
  /** 抽稀上限乘数（L3 起加密抽稀）。 */
  thinFactor: number;
  /** L4：整层标注关闭。 */
  disable: boolean;
  actions: string[];
}

/** 确定性选级（§0.4 阶梯：缩字号 → 去晕圈 → 抽稀 → 关闭）。 */
export function resolveDegrade(inkRatio: number): DegradeDecision {
  if (inkRatio > LABEL_EXTREME_RATIO) {
    return { level: 4, inkRatio, sizeFactor: 1, haloFactor: 1, thinFactor: 1, disable: true, actions: ["label_disabled"] };
  }
  if (inkRatio > LABEL_SEVERE_RATIO) {
    return { level: 3, inkRatio, sizeFactor: 0.85, haloFactor: 0, thinFactor: 0.5, disable: false, actions: ["font_shrunk", "halo_removed", "thin_intensified"] };
  }
  if (inkRatio > LABEL_FAIL_RATIO) {
    return { level: 2, inkRatio, sizeFactor: 0.85, haloFactor: 0, thinFactor: 1, disable: false, actions: ["font_shrunk", "halo_removed"] };
  }
  if (inkRatio > LABEL_WARN_RATIO) {
    return { level: 1, inkRatio, sizeFactor: 0.85, haloFactor: 1, thinFactor: 1, disable: false, actions: ["font_shrunk"] };
  }
  return { level: 0, inkRatio, sizeFactor: 1, haloFactor: 1, thinFactor: 1, disable: false, actions: [] };
}

// ── 样式自适应（P5）────────────────────────────────────────────────────
export interface AdaptiveLabelStyle {
  textColor: string;
  haloColor: string;
  haloWidth: number;
  /** text-max-width（em；CJK 收窄换行）。 */
  textMaxWidth: number;
  letterSpacing: number;
  cjk: boolean;
  darkBasemap: boolean;
}

/** #rrggbb / #rgb / rgb(...) → [0,1] 亮度（Rec.709 加权）；不可解析 → 1（浅色）。 */
export function parseColorLuminance(color: unknown): number {
  if (typeof color !== "string") return 1;
  const hex = color.trim();
  const m = /^#([0-9a-f]{3,8})$/i.exec(hex);
  let r = 0;
  let g = 0;
  let b = 0;
  if (m) {
    const body = m[1];
    if (body.length === 3 || body.length === 4) {
      r = parseInt(body[0] + body[0], 16) / 255;
      g = parseInt(body[1] + body[1], 16) / 255;
      b = parseInt(body[2] + body[2], 16) / 255;
    } else if (body.length >= 6) {
      r = parseInt(body.slice(0, 2), 16) / 255;
      g = parseInt(body.slice(2, 4), 16) / 255;
      b = parseInt(body.slice(4, 6), 16) / 255;
    } else {
      return 1;
    }
  } else {
    const rgb = /^rgba?\(([^)]+)\)$/i.exec(hex);
    if (!rgb) return 1;
    const parts = rgb[1].split(",").map((s) => parseFloat(s));
    if (parts.length < 3 || parts.slice(0, 3).some((v) => !Number.isFinite(v))) return 1;
    r = Math.min(Math.max(parts[0] / 255, 0), 1);
    g = Math.min(Math.max(parts[1] / 255, 0), 1);
    b = Math.min(Math.max(parts[2] / 255, 0), 1);
  }
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** 底图背景亮度：style.layers 里第一个 background 层的 background-color。 */
export function basemapLuminance(style: { layers?: Array<{ type?: string; paint?: Record<string, unknown> }> } | null | undefined): number {
  const layers = style?.layers ?? [];
  for (const l of layers) {
    if (l?.type === "background") {
      return parseColorLuminance((l.paint ?? {})["background-color"]);
    }
  }
  return 1; // 缺省按浅色底（#1007 黑字白晕契约）
}

/**
 * 标注样式解析：显式 color/haloColor 恒胜；haloMode=auto 时深底反转
 * 配色（浅字深晕）且晕宽随亮度加深（+0.25 —— 深底上文字与底色对比
 * 依赖晕圈更强），浅底维持 #1007 契约（黑字白晕、基础晕宽）。CJK 只动
 * 换行/字距/晕宽（**不动字号** —— 字号绝对基准归 06 线符号律）。
 */
export function resolveLabelStyle(
  label: MapSpecLayerLabel,
  strategy: LabelStrategySpec,
  opts: { luminance: number; cjk: boolean; degrade: DegradeDecision },
): AdaptiveLabelStyle {
  const dark = opts.luminance < 0.45;
  const explicitColor = typeof label.color === "string" ? label.color : null;
  const explicitHalo = typeof label.haloColor === "string" ? label.haloColor : null;
  const auto = strategy.haloMode === "auto";
  let textColor: string;
  let haloColor: string;
  if (explicitColor) {
    textColor = explicitColor;
  } else if (auto && dark) {
    textColor = "#f5f7fa";
  } else {
    textColor = "#000000";
  }
  if (explicitHalo) {
    haloColor = explicitHalo;
  } else if (auto && dark) {
    haloColor = "#101418";
  } else {
    haloColor = "#ffffff";
  }
  const baseHalo = typeof label.haloWidth === "number" ? label.haloWidth : 1;
  // 晕宽随底图亮度：auto + 深底 +0.25（浅底保持基准）；CJK 再 +0.25。
  let haloWidth = baseHalo;
  if (auto && dark) haloWidth += 0.25;
  if (opts.cjk) haloWidth += 0.25;
  return {
    textColor,
    haloColor,
    haloWidth: Math.max(haloWidth * opts.degrade.haloFactor, 0),
    textMaxWidth: opts.cjk ? 8 : 16,
    letterSpacing: opts.cjk ? 0 : 0.05,
    cjk: opts.cjk,
    darkBasemap: dark,
  };
}

// ── label-only 变更检测（换字段不重建图层的快路径判据）─────────────────
const LABEL_OWN_KEYS = new Set(["label", "labelField", "labelSize", "labelColor"]);

function stripLabelKeys(value: unknown): string {
  if (!value || typeof value !== "object") return JSON.stringify(value ?? null);
  const obj = value as Record<string, unknown>;
  const out: Record<string, unknown> = {};
  for (const k of Object.keys(obj).sort()) {
    if (LABEL_OWN_KEYS.has(k)) continue;
    out[k] = obj[k];
  }
  return JSON.stringify(out);
}

function labelPartKey(layer: MapSpecLayer): string {
  return JSON.stringify({
    label: layer.label ?? null,
    labelField: layer.layout ? (layer.layout as Record<string, unknown>).labelField ?? null : null,
    labelSize: layer.layout ? (layer.layout as Record<string, unknown>).labelSize ?? null : null,
    labelColor: layer.layout ? (layer.layout as Record<string, unknown>).labelColor ?? null : null,
  });
}

/**
 * recompile 变更是否**只动了标注声明**：主体（id/type/source/paint/
 * layout 除 label 键/filter/…）逐位相等且标注声明确有变化。
 * 判定走规范化 JSON（键序无关），确定性。
 */
export function labelOnlyLayerChange(prev: MapSpecLayer, next: MapSpecLayer): boolean {
  return stripLabelKeys(prev) === stripLabelKeys(next) && labelPartKey(prev) !== labelPartKey(next);
}

// ── runtime 事件（evidence）───────────────────────────────────────────
export interface LabelRuntimeEvent {
  seq: number;
  layerId: string;
  kind: "degrade" | "thin" | "band" | "refield" | "disabled" | "skipped";
  level?: number;
  detail: Record<string, unknown>;
}

/** label 子层定义的组装输入/输出（runtime 与测试共用同一形状）。 */
export interface LabelLayerBuild {
  paint: Record<string, unknown>;
  layout: Record<string, unknown>;
  filter: unknown[] | null;
  minzoom?: number;
  maxzoom?: number;
  events: LabelRuntimeEvent[];
  selection: TopLabelSelection;
  degrade: DegradeDecision;
  style: AdaptiveLabelStyle;
}
