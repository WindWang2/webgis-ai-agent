/**
 * 要素级标注 authoring → MapLibre 表达式投影（G16，live 面）。
 *
 * 后端 app/lib/cartography/label_authoring.py 的运行时消费孪生：身份
 * 解析（FEATURE_ID_KEYS 链 + 顶层 feature.id，str() 归一 —— `1` ≡ `"1"`）
 * 与失效语义（orphan 不投影 + 事件披露）与后端同口径。导出面消费孪生见
 * mapspec-compiler/label-obstacles.ts（同源身份解析）。
 *
 * 表达式口径：
 * - 文字覆写/隐藏/偏移按**解析键分组** match（顶层 id 用 ["id"] 表达式，
 *   属性键用 ["get", key]）；≤3 个不同键（bounded），超出部分如实披露
 *   （partial 投影事件），绝不静默丢。
 * - offset 为 canonical em（MapLibre text-offset 原生单位）；导出面
 *   em × font_px 同式换算 —— live/导出相对位置一致。
 * - pinned 子层由 runtime 另行创建（text-allow-overlap:true，本模块只
 *   出表达式物料）。
 */
import type { LabelAuthoringOverride } from '@/lib/mapspec-compiler/types';

export const FEATURE_ID_KEYS = [
  'id', 'OBJECTID', 'fid', 'FID', 'osm_id', '@id', 'featureId', 'feature_id',
] as const;

/** 单要素身份（与 backend resolve_feature_identity / label-obstacles 同源）。 */
export function resolveFeatureIdentity(
  feature: { id?: unknown; properties?: Record<string, unknown> | null },
): { featureId: string | null; idKey: string | null } {
  const top = feature.id;
  if (typeof top === 'string' || typeof top === 'number') {
    return { featureId: String(top), idKey: null };
  }
  const props = feature.properties;
  if (props && typeof props === 'object') {
    for (const key of FEATURE_ID_KEYS) {
      const v = props[key];
      if (typeof v === 'string' || typeof v === 'number') {
        return { featureId: String(v), idKey: key };
      }
    }
  }
  return { featureId: null, idKey: null };
}

export interface AuthoringProjection {
  /** 全部覆写（原样）。 */
  overrides: LabelAuthoringOverride[];
  /** 身份复核后在当前数据找到目标的覆写。 */
  active: LabelAuthoringOverride[];
  /** orphan（当前数据找不到目标）→ 不投影 + 事件披露。 */
  orphan: LabelAuthoringOverride[];
  hiddenIds: string[];
  pinned: LabelAuthoringOverride[];
  textById: Map<string, string>;
  offsetById: Map<string, [number, number]>;
  /** 解析键 → [featureId...]（表达式分组物料）。 */
  idsByKeys: Map<string | null, string[]>;
  /** 表达式不可达的覆写目标（>3 个不同解析键 / 危险 match 字面量名）
   *  —— 有名有姓，runtime 发 partial 披露事件，绝不静默丢。 */
  unprojectable: string[];
}

type AnyFeature = { id?: unknown; properties?: Record<string, unknown> | null };

/** 覆写表 × 当前数据 → 投影物料（纯函数）。 */
export function projectAuthoring(
  overrides: readonly LabelAuthoringOverride[] | undefined | null,
  features: readonly AnyFeature[] | null | undefined,
): AuthoringProjection {
  const out: AuthoringProjection = {
    overrides: overrides ? [...overrides] : [],
    active: [], orphan: [], hiddenIds: [], pinned: [],
    textById: new Map(), offsetById: new Map(), idsByKeys: new Map(),
    unprojectable: [],
  };
  if (!overrides || overrides.length === 0) return out;
  if (!features) {
    // 数据不可复核（外部源）：全部按 unverified 投影（消费侧权威口径）。
    out.active = [...overrides];
  } else {
    const index = new Map<string, string | null>();
    for (const f of features) {
      const { featureId, idKey } = resolveFeatureIdentity(f);
      if (featureId !== null && !index.has(featureId)) index.set(featureId, idKey);
    }
    for (const ov of overrides) {
      if (index.has(ov.featureId)) out.active.push(ov);
      else out.orphan.push(ov);
    }
  }
  for (const ov of out.active) {
    if (ov.hidden === true) out.hiddenIds.push(ov.featureId);
    if (ov.pinned === true) out.pinned.push(ov);
    if (typeof ov.text === 'string' && ov.text.length > 0) {
      out.textById.set(ov.featureId, ov.text);
    }
    if (ov.offset && typeof ov.offset.dx === 'number' && typeof ov.offset.dy === 'number') {
      out.offsetById.set(ov.featureId, [ov.offset.dx, ov.offset.dy]);
    }
    const idKey = ov.idKey ?? null;
    const bucket = out.idsByKeys.get(idKey) ?? [];
    bucket.push(ov.featureId);
    out.idsByKeys.set(idKey, bucket);
  }
  // 表达式可达性：>3 个不同解析键或危险 match 字面量 → 逐个点名。
  const keyList = Array.from(out.idsByKeys.keys());
  if (keyList.length > 3) {
    for (const k of keyList.slice(3)) {
      out.unprojectable.push(...(out.idsByKeys.get(k) ?? []));
    }
  }
  for (const ids of out.idsByKeys.values()) {
    for (const fid of ids) {
      if (UNSAFE_MATCH_LABELS.has(fid)) out.unprojectable.push(fid);
    }
  }
  return out;
}

/**
 * 解析键 → 值表达式（顶层 id 用 ["id"]；属性键用 ["get", key]）。
 * R1-P1：恒 to-string —— MapLibre match 是严格类型相等（数值 id/属性
 * 永不等于字符串标签），featureId 的 str() 归一只有经 to-string 才在
 * 表达式侧成立（`1` ≡ `"1"` 契约）。
 */
function keyExpr(idKey: string | null): unknown[] {
  return idKey === null
    ? ['to-string', ['id']]
    : ['to-string', ['get', idKey]];
}

/** match 字面量危险名（原型链属性 —— MapLibre Match.parse 的 cases
 * 对象会被继承属性污染，整个表达式被拒）：这些 id 不进 match（丢弃 +
 * 由 runtime 披露事件）。 */
const UNSAFE_MATCH_LABELS = new Set([
  'toString', 'constructor', 'valueOf', 'hasOwnProperty', '__proto__',
  'isPrototypeOf', 'propertyIsEnumerable', 'toLocaleString',
]);

/** 分组 match：ids → values（成对），default 兜底。多键时 case 链。 */
function groupedMatch(
  idsByKeys: Map<string | null, string[]>,
  valueOf: (fid: string) => unknown,
  fallback: unknown,
  maxKeys = 3,
): unknown | null {
  const keys = Array.from(idsByKeys.keys())
    .filter((k) => (idsByKeys.get(k) ?? []).length > 0)
    .filter((k) => (idsByKeys.get(k) ?? []).every(
      (fid) => !UNSAFE_MATCH_LABELS.has(fid)));
  if (keys.length === 0) return null;
  const usable = keys.slice(0, maxKeys);
  const matchOf = (k: string | null): unknown[] => {
    const ids = (idsByKeys.get(k) ?? []).slice().sort();
    const pairs: unknown[] = ['match', keyExpr(k)];
    for (const id of ids) pairs.push(id, valueOf(id));
    pairs.push(fallback);
    return pairs;
  };
  if (usable.length === 1) return matchOf(usable[0]);
  // 多键：case 链 —— 每组 match 的 fallback 用哨兵值判断是否命中。
  let expr: unknown = fallback;
  for (let i = usable.length - 1; i >= 0; i -= 1) {
    const m = matchOf(usable[i]);
    // 组内未命中（fallback 哨兵）→ 落到下一组（expr）。
    expr = ['case', ['!=', m, '\u0000__fallback__'], m, expr];
    // 用哨兵替换组内 fallback：重建（matchOf 尾元素即 fallback）。
    (m as unknown[])[m.length - 1] = '\u0000__fallback__';
  }
  return expr;
}

/** 文字覆写 → text-field 表达式（无覆写 → null = 保持字段表达式）。 */
export function buildTextFieldExpr(
  proj: AuthoringProjection, field: string,
): unknown | null {
  if (proj.textById.size === 0) return null;
  return groupedMatch(
    filterKeys(proj, (fid) => proj.textById.has(fid)),
    (fid) => proj.textById.get(fid) ?? '',
    ['get', field],
  );
}

/** 隐藏 ids → 排除 filter 片段（无隐藏 → null）。 */
export function buildHiddenExcludeFilter(proj: AuthoringProjection): unknown | null {
  if (proj.hiddenIds.length === 0) return null;
  return groupedMatch(
    filterKeys(proj, (fid) => proj.hiddenIds.includes(fid)),
    () => false,
    true,
  );
}

/** 偏移覆写 → text-offset 表达式（无 → null）。 */
export function buildTextOffsetExpr(
  proj: AuthoringProjection, onlyPinned: boolean,
): unknown | null {
  const ids = Array.from(proj.offsetById.keys()).filter((fid) => {
    const ov = proj.active.find((o) => o.featureId === fid);
    return ov && (!onlyPinned || ov.pinned === true);
  });
  if (ids.length === 0) return null;
  return groupedMatch(
    filterKeys(proj, (fid) => ids.includes(fid)),
    (fid) => proj.offsetById.get(fid) ?? [0, 0],
    [0, 0],
  );
}

/** pinned ids → 只含 pinned 的 filter（hidden 恒胜 —— 与导出侧
 * `pinned and not hidden` 同口径：矛盾组合按用户显式隐藏处理）。 */
export function buildPinnedFilter(proj: AuthoringProjection): unknown | null {
  const pinnedIds = proj.pinned
    .filter((o) => o.hidden !== true)
    .map((o) => o.featureId);
  if (pinnedIds.length === 0) return null;
  return groupedMatch(
    filterKeys(proj, (fid) => pinnedIds.includes(fid)),
    () => true,
    false,
  );
}

/** 非 pinned 且非 hidden 的活动覆写目标 → 排除于 pinned 子层。 */
export function buildAutoFilter(proj: AuthoringProjection): unknown | null {
  if (proj.pinned.length === 0 && proj.hiddenIds.length === 0) return null;
  const pinnedIds = new Set(proj.pinned.map((o) => o.featureId));
  const hiddenIds = new Set(proj.hiddenIds);
  return groupedMatch(
    filterKeys(proj, (fid) => pinnedIds.has(fid) || hiddenIds.has(fid)),
    (fid) => !(pinnedIds.has(fid) || hiddenIds.has(fid)),
    true,
  );
}

function filterKeys(
  proj: AuthoringProjection, keep: (fid: string) => boolean,
): Map<string | null, string[]> {
  const outMap = new Map<string | null, string[]>();
  for (const [k, ids] of proj.idsByKeys) {
    const kept = ids.filter(keep);
    if (kept.length > 0) outMap.set(k, kept);
  }
  return outMap;
}

/** em → live px（canonical 换算单源 —— 拖拽捕获用反向 pxToEm）。 */
export function emToPx(em: number, fontPx: number): number {
  return em * fontPx;
}

export function pxToEm(px: number, fontPx: number): number {
  return fontPx > 0 ? px / fontPx : 0;
}
