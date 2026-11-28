/**
 * 读者筛选谓词 ↔ 服务端 where AST ↔ MapLibre filter 表达式。
 * 单一映射实现：地图视觉筛选与数据表/统计用同一谓词（图例与数据表达一致）。
 */
import type { ReaderPredicate } from './reader-types';

/** 服务端 feature_pages 的 leaf where AST（subset：读者面支持的算子）。 */
export interface WhereLeaf {
  field: string;
  op: 'eq' | 'ne' | 'gte' | 'lte' | 'gt' | 'lt' | 'contains';
  value: string | number | boolean | null;
}

export type WhereAst = WhereLeaf;

/** 谓词列表 → 服务端 where AST（多谓词 = and 组合；后端 AST 支持 and）。 */
export function predicatesToWhere(predicates: ReaderPredicate[]): WhereAst | { op: 'and'; terms: WhereAst[] } | null {
  const leaves: WhereAst[] = predicates
    .filter((p) => p.field && p.value !== undefined && p.value !== '')
    .map((p) => ({ field: p.field, op: p.op, value: p.value }));
  if (leaves.length === 0) return null;
  if (leaves.length === 1) return leaves[0];
  return { op: 'and', terms: leaves };
}

const OP_TO_MAPLIBRE: Record<WhereLeaf['op'], string> = {
  eq: '==',
  ne: '!=',
  gte: '>=',
  lte: '<=',
  gt: '>',
  lt: '<',
  contains: '==', // 字符串包含降级为精确匹配（MapLibre legacy filter 无 contains）
};

/**
 * 谓词列表 → MapLibre legacy filter 表达式（["all", ...]）。
 * 谓词为空 → null（不过滤）。
 */
export function predicatesToMapLibreFilter(predicates: ReaderPredicate[]): unknown[] | null {
  const active = predicates.filter((p) => p.field && p.value !== undefined && p.value !== '');
  if (active.length === 0) return null;
  const clauses = active.map((p) => {
    if (p.op === 'contains') {
      // MapLibre 表达式风格 index-of；legacy filter 不支持 → 表达式风格。
      return ['>=', ['index-of', ['to-string', p.value], ['get', p.field]], 0];
    }
    return [OP_TO_MAPLIBRE[p.op], ['get', p.field], p.value];
  });
  return clauses.length === 1 ? clauses[0] : ['all', ...clauses];
}

/** 谓词是否引用了白名单外字段（调用前防御；越界谓词不得发出请求）。 */
export function predicatesWithinWhitelist(
  predicates: ReaderPredicate[],
  whitelist: string[],
): boolean {
  const allowed = new Set(whitelist);
  return predicates.every((p) => allowed.has(p.field));
}

/** 谓词变化的人可读描述（aria 播报）。 */
export function describePredicates(predicates: ReaderPredicate[]): string {
  if (predicates.length === 0) return '未启用筛选';
  return predicates
    .map((p) => {
      const opLabel: Record<WhereLeaf['op'], string> = {
        eq: '等于',
        ne: '不等于',
        gte: '不小于',
        lte: '不大于',
        gt: '大于',
        lt: '小于',
        contains: '包含',
      };
      return `${p.field} ${opLabel[p.op]} ${String(p.value)}`;
    })
    .join(' 且 ');
}
