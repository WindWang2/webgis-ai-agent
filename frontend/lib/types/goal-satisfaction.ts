/**
 * GoalSatisfaction 前端类型与解析（G28 / ADR-0183 任务完成语义面）。
 *
 * 与后端 `app/services/gis_harness/goal_satisfaction/projection.py`
 * `bounded_payload` 的 task_complete SSE 载荷逐字段对应（additive wire 契约）。
 * 解析纯函数：载荷缺失/形状非法 → null（键省略零漂移，绝不渲染半张卡）。
 */

export type GoalRequirementVerdict =
  | 'fulfilled'
  | 'partial'
  | 'blocked'
  | 'not_evaluated'
  | 'failed';

export interface GoalRequirementRow {
  /** 需求 id（map / analysis:<capability> / export:<fmt> / statistics…）。 */
  id: string;
  kind: string;
  verdict: GoalRequirementVerdict;
  /** 机器可读缺失码（≤3 行，后端已截断）。 */
  missing: string[];
  /** 建议下一步（reobserve / rerun_complete / run_delivery…，可空）。 */
  next_action: string;
}

export interface GoalSatisfactionView {
  verdict: 'satisfied' | 'partial' | 'blocked' | 'failed' | 'not_evaluated';
  signal: string;
  counts: Record<string, number>;
  missing_summary: string[];
  summary_line: string;
  requirements: GoalRequirementRow[];
}

const VERDICTS = new Set([
  'satisfied',
  'partial',
  'blocked',
  'failed',
  'not_evaluated',
]);

const ROW_VERDICTS = new Set([
  'fulfilled',
  'partial',
  'blocked',
  'not_evaluated',
  'failed',
]);

const MAX_ROWS = 12;

function asBoundedString(value: unknown, limit: number): string {
  return typeof value === 'string' ? value.slice(0, limit) : '';
}

function asBoundedStrings(value: unknown, limit: number): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((v): v is string => typeof v === 'string')
    .slice(0, limit)
    .map((v) => v.slice(0, 96));
}

/**
 * task_complete 载荷 → 有界视图（纯函数；非法形状 → null）。
 * `data` 是 SSE task_complete 的整个 payload（goal_satisfaction 为其加性键）。
 */
export function parseGoalSatisfaction(data: unknown): GoalSatisfactionView | null {
  if (data === null || typeof data !== 'object') return null;
  const payload = data as Record<string, unknown>;
  const raw = payload.goal_satisfaction;
  if (raw === null || typeof raw !== 'object') return null;
  const gs = raw as Record<string, unknown>;
  const verdict = asBoundedString(gs.verdict, 24);
  if (!VERDICTS.has(verdict)) return null; // 无 verdict = 后端未投影 → 不渲染
  const countsRaw = (typeof gs.counts === 'object' && gs.counts !== null
    ? gs.counts
    : {}) as Record<string, unknown>;
  const counts: Record<string, number> = {};
  for (const [k, v] of Object.entries(countsRaw).slice(0, 8)) {
    if (typeof v === 'number' && Number.isFinite(v)) counts[k.slice(0, 24)] = v;
  }
  const requirements: GoalRequirementRow[] = [];
  if (Array.isArray(gs.requirements)) {
    for (const row of gs.requirements.slice(0, MAX_ROWS)) {
      if (row === null || typeof row !== 'object') continue;
      const r = row as Record<string, unknown>;
      const rowVerdict = asBoundedString(r.verdict, 16);
      if (!ROW_VERDICTS.has(rowVerdict)) continue;
      requirements.push({
        id: asBoundedString(r.id, 64),
        kind: asBoundedString(r.kind, 16),
        verdict: rowVerdict as GoalRequirementVerdict,
        missing: asBoundedStrings(r.missing, 3),
        next_action: asBoundedString(r.next_action, 64),
      });
    }
  }
  return {
    verdict: verdict as GoalSatisfactionView['verdict'],
    signal: asBoundedString(gs.signal, 32),
    counts,
    missing_summary: asBoundedStrings(gs.missing_summary, 6),
    summary_line: asBoundedString(gs.summary_line, 240),
    requirements,
  };
}
