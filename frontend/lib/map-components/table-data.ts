import { apiFetch } from '@/lib/api/transport';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { devOnly } from '@/lib/utils/logger';

/**
 * table_panel 的数据通道（Runtime V4 §10）。
 *
 * 双通道：
 * - tableRef（ref:stats-table-* 等 artifact）→ GET table-artifacts/{ref}，
 *   模块缓存 + in-flight 去重 + 代数订阅（chart-artifact 同款模式）；
 * - layerId（HUD 图层属性表）→ 直接读 layer.source（内联 FC），MVT 层由
 *   渲染器经 ensureLayerData(layerId, 'attribute-table') 按需水合。
 *
 * 行模型（跨视图联动的接合点）：
 * - 稳定行 id 与 map 框选/点击同一解析链（FEATURE_ID_KEYS 序），无 id 的
 *   要素用内容哈希兜底 —— table↔map 的 id 过滤投影因此可双向编译；
 * - 行数据是**引用**不是克隆（100k 行不复制 100k 对象）。
 *
 * 有界纪律：
 * - 行数上限 MAX_TABLE_ROWS（50k）—— 超限截断并如实披露 truncated；
 * - 列数上限 32；单元格字符串截断 64 字符（显示面，不进 store）。
 */

export const MAX_TABLE_ROWS = 50000;
export const MAX_TABLE_COLUMNS = 32;

/** 与 lib/store/layer-data.ts 的 FEATURE_ID_KEYS 同序（稳定要素身份单源）。 */
const ROW_ID_KEYS = ['id', 'OBJECTID', 'fid', 'osm_id', '@id', 'featureId', 'feature_id'];

export interface TableRow {
  /** 稳定行 id（FEATURE_ID_KEYS 解析或 h- 内容哈希兜底）。 */
  rowId: string;
  /** 行属性（引用要素 properties，不克隆）。 */
  props: Record<string, unknown>;
}

export interface TableModel {
  columns: string[];
  rows: TableRow[];
  /** 原始行数超过上限被截断（诚实披露）。 */
  truncated: boolean;
  totalCount: number;
}

/** 内容哈希兜底（无稳定 id 字段的要素）：短、确定性、只用于行寻址。 */
function contentHash(props: Record<string, unknown>): string {
  let h = 0;
  const keys = Object.keys(props).slice(0, 8);
  for (const k of keys) {
    const v = props[k];
    const s = `${k}:${typeof v === 'object' || v == null ? '' : String(v)}`;
    for (let i = 0; i < s.length; i++) {
      h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
    }
  }
  return `h-${(h >>> 0).toString(36)}`;
}

export function resolveRowId(props: Record<string, unknown>, topLevelId?: string | number): string {
  if (topLevelId != null && String(topLevelId).trim() !== '') return String(topLevelId).slice(0, 64);
  for (const key of ROW_ID_KEYS) {
    const v = props[key];
    if (v != null && v !== '' && (typeof v === 'string' || typeof v === 'number')) {
      return String(v).slice(0, 64);
    }
  }
  return contentHash(props);
}

function deriveColumns(records: Array<Record<string, unknown>>): string[] {
  const seen: string[] = [];
  const sample = records.slice(0, 20);
  for (const rec of sample) {
    for (const key of Object.keys(rec)) {
      if (!seen.includes(key) && key !== 'geometry') seen.push(key);
      if (seen.length >= MAX_TABLE_COLUMNS) return seen;
    }
  }
  return seen;
}

/** 记录数组 → TableModel（纯函数；行引用不克隆；超限截断）。 */
export function buildTableModel(
  records: Array<Record<string, unknown>>,
  preferredColumns?: string[],
): TableModel {
  const rows: TableRow[] = [];
  for (let i = 0; i < records.length && rows.length < MAX_TABLE_ROWS; i++) {
    const rec = records[i];
    if (!rec || typeof rec !== 'object') continue;
    rows.push({ rowId: resolveRowId(rec, (rec as { id?: unknown }).id as string | number | undefined), props: rec });
  }
  const columns = preferredColumns && preferredColumns.length
    ? preferredColumns.slice(0, MAX_TABLE_COLUMNS)
    : deriveColumns(records);
  return {
    columns,
    rows,
    truncated: records.length > MAX_TABLE_ROWS,
    totalCount: records.length,
  };
}

// ─── ref 通道（chart-artifact 同款模块缓存模式）───────────────────────────

type TablePayload = { table?: unknown } | unknown[] | null;

const cache = new Map<string, TablePayload>();
const failureAt = new Map<string, number>();
const inFlight = new Map<string, Promise<TablePayload>>();
let generation = 0;
const listeners = new Set<() => void>();
const FAILURE_TTL_MS = 30_000;

function emit(): void {
  generation += 1;
  listeners.forEach((l) => l());
}

export function subscribeTableArtifacts(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getTableArtifactsGeneration(): number {
  return generation;
}

export function resetTableArtifactCache(): void {
  cache.clear();
  failureAt.clear();
  inFlight.clear();
  emit();
}

export function getCachedTableArtifact(ref: string): TablePayload | undefined {
  const hit = cache.get(ref);
  if (hit === null) {
    const at = failureAt.get(ref) ?? 0;
    if (Date.now() - at > FAILURE_TTL_MS) {
      cache.delete(ref);
      failureAt.delete(ref);
      return undefined;
    }
  }
  return hit;
}

export async function loadTableArtifact(ref: string): Promise<TablePayload> {
  const key = ref.trim();
  if (!key) return null;
  const hit = cache.get(key);
  if (hit !== undefined) return hit;
  const pending = inFlight.get(key);
  if (pending) return pending;
  const task = (async (): Promise<TablePayload> => {
    const { sessionId, ownerToken } = getMapSpecSessionCursor();
    if (!sessionId) return null;
    try {
      const data = await apiFetch<{ table?: unknown }>(
        `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/table-artifacts/${encodeURIComponent(key)}`,
        { ownerToken, label: 'Table artifact resolve error' },
      );
      const table = (data as { table?: unknown } | null)?.table;
      cache.set(key, table ?? null);
      return table ?? null;
    } catch (e) {
      devOnly.warn(`[table-data] ref ${key} 拉取失败`, e);
      cache.set(key, null);
      failureAt.set(key, Date.now());
      return null;
    }
  })();
  inFlight.set(key, task);
  const cleanup = () => {
    inFlight.delete(key);
    emit();
  };
  void task.then(cleanup, cleanup);
  return task;
}

/** 宽容规整：{table:{columns,rows}} / {columns,rows} / 记录数组 → TableModel。 */
export function normalizeTablePayload(payload: TablePayload, preferredColumns?: string[]): TableModel | null {
  if (!payload) return null;
  let records: Array<Record<string, unknown>> | null = null;
  let columns: string[] | undefined;
  if (Array.isArray(payload)) {
    records = payload as Array<Record<string, unknown>>;
  } else if (typeof payload === 'object') {
    const obj = payload as { table?: unknown; columns?: unknown; rows?: unknown };
    const table = obj.table;
    if (Array.isArray(table)) {
      records = table as Array<Record<string, unknown>>;
    } else if (table && typeof table === 'object') {
      const t = table as { columns?: unknown; rows?: unknown };
      if (Array.isArray(t.rows)) {
        records = t.rows.map((r) => (Array.isArray(r) ? arrayToRecord(t.columns as string[], r) : r)) as Array<Record<string, unknown>>;
        columns = Array.isArray(t.columns) ? t.columns.map(String) : undefined;
      }
    } else if (Array.isArray(obj.rows)) {
      records = obj.rows as Array<Record<string, unknown>>;
      columns = Array.isArray(obj.columns) ? obj.columns.map(String) : undefined;
    }
  }
  if (!records || !records.length) return null;
  return buildTableModel(records, preferredColumns ?? columns);
}

function arrayToRecord(columns: unknown, row: unknown[]): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  const cols = Array.isArray(columns) ? columns.map(String) : [];
  for (let i = 0; i < row.length && i < cols.length; i++) out[cols[i]] = row[i];
  return out;
}

// 会话切换联动（session-cursor resetLiveState 动态 import）。
// @__PURE__ — 副作用仅注册清理钩子，由调用方触发。
export const __sessionResetHook = resetTableArtifactCache;
