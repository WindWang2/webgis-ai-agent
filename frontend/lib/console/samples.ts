'use client';

import type { QuerySpec } from '@/lib/api/data-fabric';

/**
 * 查询控制台：样例库 + 查询历史（本地持久化，ADR-0147）。
 *
 * 持久化键独立于 `geoagent-settings`（hud partialize 白名单是多线共享面，
 * 不动）。历史按条目 LRU 截断；样例库 = 内置种子 + 用户另存，均落
 * localStorage。
 */

export interface ConsoleSpec {
  where: string;
  fields: string;
  limit: number;
  order: string;
  resultMode: 'features' | 'statistics' | 'sample';
}

export interface QueryHistoryEntry {
  id: string;
  ts: number;
  targetId: string;
  targetTitle: string;
  spec: ConsoleSpec;
  /** ok | error | blocked */
  status: 'ok' | 'error' | 'blocked';
  /** 摘要（返回行数或错误信息，≤80 字符）。 */
  summary?: string;
}

export interface SampleEntry {
  id: string;
  name: string;
  description?: string;
  spec: ConsoleSpec;
  builtin?: boolean;
}

export const DEFAULT_SPEC: ConsoleSpec = {
  where: '',
  fields: '',
  limit: 100,
  order: '',
  resultMode: 'features',
};

export const BUILTIN_SAMPLES: SampleEntry[] = [
  {
    id: 'sample-prefix',
    name: '前缀过滤',
    description: '按属性前缀匹配（ILIKE），返回前 20 条',
    builtin: true,
    spec: { ...DEFAULT_SPEC, where: "name ILIKE 'A%'", limit: 20 },
  },
  {
    id: 'sample-bbox',
    name: '范围过滤（数值比较）',
    description: '数值属性范围条件 + 按字段倒序',
    builtin: true,
    spec: { ...DEFAULT_SPEC, where: 'value > 100 AND value < 1000', order: 'value DESC', limit: 50 },
  },
  {
    id: 'sample-fields',
    name: '投影裁剪',
    description: '只取两列，减少传输体积',
    builtin: true,
    spec: { ...DEFAULT_SPEC, fields: 'name, value', limit: 100 },
  },
  {
    id: 'sample-stats',
    name: '聚合统计',
    description: 'result_mode=statistics：按类型分组计数',
    builtin: true,
    spec: {
      ...DEFAULT_SPEC,
      resultMode: 'statistics',
      fields: 'type, COUNT(*) AS cnt',
      order: 'cnt DESC',
    },
  },
];

/** ConsoleSpec → 线上 QuerySpec（fields 空串不下发）。 */
export function toQuerySpec(spec: ConsoleSpec): QuerySpec {
  const out: QuerySpec = { limit: spec.limit, result_mode: spec.resultMode };
  const where = spec.where.trim();
  if (where) out.where = where;
  const fields = spec.fields
    .split(',')
    .map((f) => f.trim())
    .filter(Boolean);
  if (fields.length) out.fields = fields;
  const order = spec.order.trim();
  if (order) out.order_by = [order];
  return out;
}

const HISTORY_KEY = 'geoagent-query-console-history-v1';
const SAMPLES_KEY = 'geoagent-query-console-samples-v1';
const HISTORY_MAX = 30;

function readList<T>(key: string): T[] {
  if (typeof localStorage === 'undefined') return [];
  try {
    const raw = localStorage.getItem(key);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    return Array.isArray(parsed) ? (parsed as T[]) : [];
  } catch {
    return [];
  }
}

function writeList<T>(key: string, items: T[]): void {
  if (typeof localStorage === 'undefined') return;
  try {
    localStorage.setItem(key, JSON.stringify(items));
  } catch {
    /* 配额满/隐私模式：降级为会话内态 */
  }
}

export function loadHistory(): QueryHistoryEntry[] {
  return readList<QueryHistoryEntry>(HISTORY_KEY);
}

export function appendHistory(entry: QueryHistoryEntry): QueryHistoryEntry[] {
  const next = [entry, ...loadHistory().filter((e) => e.id !== entry.id)].slice(0, HISTORY_MAX);
  writeList(HISTORY_KEY, next);
  return next;
}

export function clearHistory(): void {
  if (typeof localStorage !== 'undefined') localStorage.removeItem(HISTORY_KEY);
}

export function loadSamples(): SampleEntry[] {
  return [...BUILTIN_SAMPLES, ...readList<SampleEntry>(SAMPLES_KEY)];
}

export function saveSample(sample: SampleEntry): SampleEntry[] {
  const user = readList<SampleEntry>(SAMPLES_KEY).filter((s) => s.id !== sample.id);
  const next = [sample, ...user].slice(0, 20);
  writeList(SAMPLES_KEY, next);
  return [...BUILTIN_SAMPLES, ...next];
}

export function deleteSample(id: string): SampleEntry[] {
  const next = readList<SampleEntry>(SAMPLES_KEY).filter((s) => s.id !== id);
  writeList(SAMPLES_KEY, next);
  return [...BUILTIN_SAMPLES, ...next];
}
