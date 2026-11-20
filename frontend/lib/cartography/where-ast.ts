/**
 * G25 画幅 where 谓词（feature_pages where AST 的 JS 同源求值器）。
 *
 * 语义逐字对齐 `app/services/feature_pages.py` 的 `evaluate_where`（后端
 * 解析器在写入侧已校验 AST —— 前端只求值，不复刻校验；畸形节点按 typed
 * 错误抛出，绝不静默放行全部要素）：
 * - 叶算子：eq/ne/in/between/contains/startswith/gte/gt/lte/lt；
 * - 组合：and/or/not；
 * - `_num_eq` 语义：bool 只与 bool 相等；数值经 float 比较；其余同型 ==
 *   （JS 中 1 === 1.0 天然成立，bool 须先排除 —— 与后端同纪律）；
 * - eq null 命中缺席/null；其余叶算子对缺席值不命中（不抛错）；
 * - between/范围算子：字符串界走字典序窗口（定宽时间编码），仅字符串
 *   字段值命中；数值界仅数值字段（bool/非有限值排除）；
 * - contains/startswith 大小写不敏感，仅字符串字段。
 *
 * 用途：FrameViewport 在内联 GeoJSON source 注入前应用帧数据绑定
 * `data.where` 过滤 —— 与后端 `resolve_frame_document` 的第 2 步同语义，
 * 使前端渲染的帧文档与服务端验证的帧文档逐位一致。
 */

export type WhereScalar = string | number | boolean | null;

export interface WhereLeafBase {
  op: string;
  field: string;
}
export interface WhereEqNe extends WhereLeafBase {
  op: 'eq' | 'ne';
  value: WhereScalar;
}
export interface WhereIn extends WhereLeafBase {
  op: 'in';
  values: WhereScalar[];
}
export interface WhereBetween extends WhereLeafBase {
  op: 'between';
  gte: string | number;
  lte: string | number;
}
export interface WhereRange extends WhereLeafBase {
  op: 'gte' | 'gt' | 'lte' | 'lt';
  value: string | number;
}
export interface WhereText extends WhereLeafBase {
  op: 'contains' | 'startswith';
  value: string;
}
export interface WhereComposite {
  op: 'and' | 'or';
  terms: WhereNode[];
}
export interface WhereNot {
  op: 'not';
  term: WhereNode;
}
export type WhereLeaf = WhereEqNe | WhereIn | WhereBetween | WhereRange | WhereText;
export type WhereNode = WhereLeaf | WhereComposite | WhereNot;

/** typed 求值失败（未知算子/畸形节点）—— 帧解析失败必须显式，不静默。 */
export class WhereAstError extends Error {
  constructor(
    public code: 'unknown_op' | 'malformed_node',
    message: string,
  ) {
    super(message);
    this.name = 'WhereAstError';
  }
}

/** bool 只与 bool 相等；数值 float 比较；其余同型 ===（后端 _num_eq 同源）。 */
function numEq(a: unknown, b: unknown): boolean {
  if (typeof a === 'boolean' || typeof b === 'boolean') {
    return typeof a === 'boolean' && typeof b === 'boolean' && a === b;
  }
  if (typeof a === 'number' && typeof b === 'number') {
    return a === b; // JS 数值比较即 float 比较（1 === 1.0）
  }
  return typeof a === typeof b && a === b;
}

const RANGE_OPS = new Set(['gte', 'gt', 'lte', 'lt']);

/**
 * 已校验 AST × 单要素 properties → bool。缺席/null 语义与后端一致：
 * eq null 命中缺席或 null；其余叶算子对缺席值不命中。
 */
export function evaluateWhereAst(
  where: WhereNode,
  feature: { properties?: Record<string, unknown> } | null | undefined,
): boolean {
  const op = (where as { op?: unknown }).op;
  if (op === 'and' || op === 'or') {
    const terms = (where as WhereComposite).terms;
    if (!Array.isArray(terms)) throw new WhereAstError('malformed_node', `${op} requires terms[]`);
    return op === 'and'
      ? terms.every((t) => evaluateWhereAst(t, feature))
      : terms.some((t) => evaluateWhereAst(t, feature));
  }
  if (op === 'not') {
    const term = (where as WhereNot).term;
    if (!term) throw new WhereAstError('malformed_node', 'not requires term');
    return !evaluateWhereAst(term, feature);
  }
  if (typeof op !== 'string') throw new WhereAstError('malformed_node', 'node missing op');

  const props = feature && typeof feature === 'object' ? feature.properties : null;
  const field = (where as WhereLeafBase).field;
  const value = props && typeof props === 'object' ? (props as Record<string, unknown>)[field] : undefined;

  if (op === 'eq' || op === 'ne') {
    const target = (where as WhereEqNe).value;
    if (op === 'eq') {
      if (target === null || target === undefined) return value === null || value === undefined;
      return value !== null && value !== undefined && numEq(value, target);
    }
    if (target === null || target === undefined) return value !== null && value !== undefined;
    return value === null || value === undefined || !numEq(value, target);
  }
  if (op === 'in') {
    const values = (where as WhereIn).values;
    if (!Array.isArray(values)) throw new WhereAstError('malformed_node', 'in requires values[]');
    for (const t of values) {
      if (t === null || t === undefined) {
        if (value === null || value === undefined) return true;
      } else if (value !== null && value !== undefined && numEq(value, t)) {
        return true;
      }
    }
    return false;
  }
  if (op === 'between') {
    const { gte, lte } = where as WhereBetween;
    if (typeof gte === 'string') {
      const lo = gte;
      const hi = lte as string;
      return typeof value === 'string' && lo <= value && value <= hi;
    }
    if (typeof value === 'boolean' || typeof value !== 'number' || !Number.isFinite(value)) {
      return false;
    }
    const lo = gte as number;
    const hi = lte as number;
    return lo <= value && value <= hi;
  }
  if (RANGE_OPS.has(op)) {
    const bound = (where as WhereRange).value;
    if (typeof bound === 'string') {
      if (typeof value !== 'string') return false;
    } else {
      if (typeof value === 'boolean' || typeof value !== 'number' || !Number.isFinite(value)) {
        return false;
      }
    }
    if (op === 'gte') return (value as string | number) >= bound;
    if (op === 'gt') return (value as string | number) > bound;
    if (op === 'lte') return (value as string | number) <= bound;
    return (value as string | number) < bound;
  }
  if (op === 'contains' || op === 'startswith') {
    if (typeof value !== 'string') return false;
    const needle = ((where as WhereText).value ?? '').toLowerCase();
    const haystack = value.toLowerCase();
    return op === 'startswith' ? haystack.startsWith(needle) : needle.length === 0 || haystack.includes(needle);
  }
  throw new WhereAstError('unknown_op', `unknown filter op: ${String(op)}`);
}

export interface GeoJsonLikeFeature {
  properties?: Record<string, unknown>;
}

/**
 * 批量过滤（帧文档解析的前端等价物）。where 为 null/undefined → 原样返回
 * （无过滤步骤）。任何求值错误向上抛（typed WhereAstError）。
 */
export function filterFeaturesByWhere<
  F extends GeoJsonLikeFeature,
>(features: F[], where: WhereNode | null | undefined): F[] {
  if (!where) return features;
  return features.filter((f) => evaluateWhereAst(where, f));
}
