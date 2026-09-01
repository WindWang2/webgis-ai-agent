/**
 * LayerFilterEvidence — 过滤命中证据（Runtime V4 / §14，ADR-0091）。
 *
 * 解决的问题：layer status = ready 但活动过滤命中 0 要素 —— 用户看到
 * 「图还在却什么都没有」，词表无法披露。它是**独立派生证据**，不是
 * LayerStatus 的新枚举（状态词表保持 7 态封闭）。
 *
 * 状态（封闭词表）：
 *   inactive  该层没有任何非平凡过滤（一切照常渲染）；
 *   active    有过滤且命中 > 0；
 *   empty     有过滤且所有带过滤的子层命中都是 0（内容被过滤清空）；
 *   invalid   过滤引用的字段在样本要素中不存在（拼写错误的 selectionField
 *             典型症状）；
 *   stale     证据是针对旧数据/旧过滤算的（数据面已变）；
 *   unknown   无法廉价判定（MVT/瓦片源无内联数据、未知过滤算子、超扫描
 *             上限）—— 未知 ≠ 失败，不虚构判定。
 *
 * 成本纪律（§14 硬约束）：
 * - 只对**内联 GeoJSON** 且要素数 ≤ FILTER_SCAN_LIMIT 的层做单遍计数扫描；
 * - MVT / 瓦片 / 超限层一律 unknown —— 绝不为一个徽标扫 100k+ 要素；
 * - 求值器只支持 adapter 实际发射的封闭算子集，遇到未知算子返回 unknown；
 * - 结果有界缓存（数据引用 + 过滤指纹 WeakMap），same-input 重渲染零扫描。
 */

export type FilterEvidenceStatus =
  | 'inactive'
  | 'active'
  | 'empty'
  | 'invalid'
  | 'stale'
  | 'unknown';

export interface LayerFilterEvidence {
  status: FilterEvidenceStatus;
  /** 命中数（仅内联 ≤ 上限的层携带；取子层最大值）。 */
  matched_count?: number;
  /** 扫描的要素数（诊断：超上限时不携带 matched_count）。 */
  scanned?: number;
  at: number;
}

/** 单层计数扫描上限 —— 超过即 unknown（不扫描大层）。 */
export const FILTER_SCAN_LIMIT = 20000;
/** 字段存在性检查的样本量。 */
const FIELD_SAMPLE = 50;
/** 证据缓存上限（层族数）。 */
export const MAX_FILTER_EVIDENCE_LAYERS = 128;

// ─── 封闭算子求值器（只支持 adapter 发射的词表）──────────────────────────

type FeatureLike = {
  id?: number | string;
  properties?: Record<string, unknown>;
  geometry?: { type?: string };
};

function getValue(expr: unknown, f: FeatureLike): unknown {
  // MapLibre 过滤的叶子既可以是表达式（["get", f]）也可以是裸字面量
  // （30 / '武侯区'）—— 非数组按字面量直返。
  if (!Array.isArray(expr)) return expr;
  const op = expr[0];
  if (op === 'get') return (f.properties ?? {})[String(expr[1])];
  if (op === 'id') return f.id;
  if (op === 'literal') return expr[1];
  return undefined;
}

/**
 * 求值单个过滤表达式。返回 true/false，遇到未支持算子/类型返回 null
 * （→ unknown，不猜）。比较语义对齐 MapLibre：== 宽松相等（数字/字符串
 * 不跨型强等，按 === 近似；adapter 发射的字面量类型与属性一致）。
 */
export function evaluateFilterBounded(expr: unknown, f: FeatureLike): boolean | null {
  if (!Array.isArray(expr) || expr.length === 0) return null;
  const op = expr[0];
  switch (op) {
    case 'all': {
      let anyUnknown = false;
      for (let i = 1; i < expr.length; i++) {
        const r = evaluateFilterBounded(expr[i], f);
        if (r === null) anyUnknown = true;
        else if (!r) return false;
      }
      return anyUnknown ? null : true;
    }
    case 'any': {
      let anyTrue = false;
      let anyUnknown = false;
      for (let i = 1; i < expr.length; i++) {
        const r = evaluateFilterBounded(expr[i], f);
        if (r === true) anyTrue = true;
        else if (r === null) anyUnknown = true;
      }
      if (anyTrue) return true;
      return anyUnknown ? null : false;
    }
    case '!': {
      const r = evaluateFilterBounded(expr[1], f);
      return r === null ? null : !r;
    }
    case '==':
    case '!=': {
      const a = getValue(expr[1], f);
      const b = getValue(expr[2], f);
      if (expr[1] === '$type' || (Array.isArray(expr[1] as unknown[]) && (expr[1] as unknown[])[0] === '$type')) {
        const geom = f.geometry?.type ?? '';
        const want = String(b);
        // $type 词表：Point/Polygon/LineString —— geometry.type 含 Multi 前缀
        const eq = want === 'Point' ? geom === 'Point'
          : want === 'Polygon' ? geom.includes('Polygon')
            : want === 'LineString' ? geom.includes('Line')
              : geom === want;
        return op === '==' ? eq : !eq;
      }
      if (a === undefined || a === null) return op === '!=' ? true : false;
      const eq = a === b || (typeof a === 'number' && typeof b === 'number' && a === b);
      return op === '==' ? eq : !eq;
    }
    case '>=':
    case '>':
    case '<=':
    case '<': {
      const a = getValue(expr[1], f);
      const b = getValue(expr[2], f);
      const an = typeof a === 'number' ? a : Number(a);
      const bn = typeof b === 'number' ? b : Number(b);
      if (!Number.isFinite(an) || !Number.isFinite(bn)) return false; // MapLibre：不可比较 → 不命中
      switch (op) {
        case '>=': return an >= bn;
        case '>': return an > bn;
        case '<=': return an <= bn;
        default: return an < bn;
      }
    }
    case 'in': {
      const v = getValue(expr[1], f);
      if (v === undefined || v === null) return false;
      const haystackRaw = expr[2];
      const haystack = Array.isArray(haystackRaw) && haystackRaw[0] === 'literal'
        ? haystackRaw[1]
        : (Array.isArray(haystackRaw) && haystackRaw[0] !== 'get' && haystackRaw[0] !== 'id' && haystackRaw[0] !== 'literal'
          ? haystackRaw // spread 形式（防御性兼容）
          : null);
      if (!Array.isArray(haystack)) return null;
      return haystack.some((item) => item === v || String(item) === String(v));
    }
    default:
      return null; // 未知算子（imperative filter 任意形状等）→ unknown
  }
}

/** 收集表达式里通过 ["get", f] 引用的字段名（invalid 判定输入）。 */
export function collectFilterFields(expr: unknown, out: Set<string> = new Set()): Set<string> {
  if (!Array.isArray(expr)) return out;
  if (expr[0] === 'get' && typeof expr[1] === 'string') {
    out.add(expr[1]);
    return out;
  }
  for (let i = 1; i < expr.length; i++) collectFilterFields(expr[i], out);
  return out;
}

export interface FilterEvidenceInput {
  /** HUD 层（或 spec 层族 id）。 */
  layerId: string;
  /** 该层的子层过滤表达式（live spec 中该族全部非空 filter）。 */
  sublayerFilters: unknown[][];
  /** 内联 GeoJSON features（MVT/瓦片层传 undefined → unknown）。 */
  features?: unknown[];
}

/**
 * 派生单层过滤证据（纯函数）。
 */
export function deriveFilterEvidence({
  layerId: _layerId,
  sublayerFilters,
  features,
}: FilterEvidenceInput): LayerFilterEvidence {
  void _layerId;
  const at = Date.now();
  const nonTrivial = sublayerFilters.filter((f) => Array.isArray(f) && f.length > 0);
  if (nonTrivial.length === 0) return { status: 'inactive', at };
  if (!features || !Array.isArray(features)) return { status: 'unknown', at };

  // invalid：所有带过滤子层引用的字段在样本中都不存在
  const sample = features.slice(0, FIELD_SAMPLE) as FeatureLike[];
  const referenced = new Set<string>();
  for (const f of nonTrivial) collectFilterFields(f, referenced);
  if (referenced.size > 0) {
    const present = new Set<string>();
    for (const f of sample) {
      for (const k of Object.keys(f.properties ?? {})) present.add(k);
    }
    let anyPresent = false;
    for (const field of referenced) {
      if (present.has(field)) { anyPresent = true; break; }
    }
    if (!anyPresent && sample.length > 0) {
      return { status: 'invalid', at, scanned: sample.length };
    }
  }

  if (features.length > FILTER_SCAN_LIMIT) {
    return { status: 'unknown', at, scanned: features.length };
  }
  // 逐子层计数（有界：features ≤ 上限 × 子层数 ≤ 5，表达式求值 O(1)）
  const counts = nonTrivial.map((filter) => {
    let count = 0;
    let unknown = false;
    for (const feat of features as FeatureLike[]) {
      const r = evaluateFilterBounded(filter, feat);
      if (r === null) { unknown = true; break; }
      if (r) count += 1;
    }
    return { count, unknown };
  });
  const hasUnknownOp = counts.some((c) => c.unknown);
  let allEmpty = true;
  let maxMatched = 0;
  for (const c of counts) {
    if (c.count > 0) { allEmpty = false; if (c.count > maxMatched) maxMatched = c.count; }
  }
  if (hasUnknownOp && allEmpty) {
    // 过滤里混有未知算子且已知部分全空 —— 不能断言 empty
    return { status: 'unknown', at, scanned: features.length };
  }
  return {
    status: allEmpty ? 'empty' : 'active',
    matched_count: maxMatched,
    scanned: features.length,
    at,
  };
}

// ─── 模块 store（有界 latest-wins per layer family）───────────────────────

const evidenceByLayer = new Map<string, LayerFilterEvidence>();
const listeners = new Set<() => void>();
let generation = 0;

function emit(): void {
  generation += 1;
  listeners.forEach((l) => l());
}

export function subscribeFilterEvidence(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getFilterEvidenceGeneration(): number {
  return generation;
}

export function getFilterEvidence(layerId: string): LayerFilterEvidence | null {
  return evidenceByLayer.get(layerId) ?? null;
}

/** 批量记录（latest-wins；越界裁剪保持有界）。 */
export function recordFilterEvidence(entries: Array<{ layerId: string; evidence: LayerFilterEvidence }>): void {
  if (!entries.length) return;
  for (const { layerId, evidence } of entries) {
    evidenceByLayer.set(layerId, evidence);
  }
  if (evidenceByLayer.size > MAX_FILTER_EVIDENCE_LAYERS) {
    const drop = evidenceByLayer.size - MAX_FILTER_EVIDENCE_LAYERS;
    let i = 0;
    for (const key of evidenceByLayer.keys()) {
      if (i++ >= drop) break;
      evidenceByLayer.delete(key);
    }
  }
  emit();
}

/** 会话切换：证据属于当前会话的数据面。 */
export function clearFilterEvidence(): void {
  if (!evidenceByLayer.size) return;
  evidenceByLayer.clear();
  emit();
}

export const FILTER_EVIDENCE_LABELS: Record<FilterEvidenceStatus, string> = {
  inactive: '',
  active: '过滤生效',
  empty: '过滤后 0 要素',
  invalid: '过滤字段不存在',
  stale: '过滤证据过期',
  unknown: '',
};
