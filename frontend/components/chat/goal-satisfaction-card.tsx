'use client';

/**
 * GoalSatisfactionCard — 任务完成语义卡（G28 / ADR-0183 P4）。
 *
 * 渲染条件：本轮助手消息挂载 goalSatisfaction（task_complete SSE 载荷
 * 的 goal_satisfaction 加性键；与 SSE/最终文本消费同一持久化裁决，A24）。
 * 行为（只展示，无交互）：
 *  - 顶部裁决徽标：satisfied / partial / blocked / failed / not_evaluated；
 *  - 逐需求行：需求 id + verdict 徽标 + 缺失码（≤3）+ 建议动作 —— 让
 *    「计算完成、地图等待显示」与「任务失败」一眼可分；
 *  - 底部 summary_line（后端单行投影，有界）。
 * 红线：本卡不判成功 —— verdict 是后端持久化块的只读投影；载荷非法
 * （parseGoalSatisfaction → null）时卡片不挂载（零漂移）。
 */

import type { ReactElement } from 'react';
import { AlertCircle, CheckCircle2, CircleDashed, Clock, OctagonAlert } from 'lucide-react';
import { useT } from '@/lib/i18n/useT';
import type { GoalRequirementRow, GoalSatisfactionView } from '@/lib/types/goal-satisfaction';

export interface GoalSatisfactionCardProps {
  view: GoalSatisfactionView;
}

const VERDICT_TONE: Record<
  GoalSatisfactionView['verdict'] | 'fulfilled',
  { chip: string; icon: ReactElement; soft: string }
> = {
  satisfied: {
    chip: 'text-status-success',
    soft: 'bg-status-success-soft',
    icon: <CheckCircle2 size={12} />,
  },
  fulfilled: {
    chip: 'text-status-success',
    soft: 'bg-status-success-soft',
    icon: <CheckCircle2 size={12} />,
  },
  partial: {
    chip: 'text-status-warning',
    soft: 'bg-status-warning-soft',
    icon: <Clock size={12} />,
  },
  blocked: {
    chip: 'text-status-warning',
    soft: 'bg-status-warning-soft',
    icon: <OctagonAlert size={12} />,
  },
  failed: {
    chip: 'text-status-critical',
    soft: 'bg-status-critical-soft',
    icon: <AlertCircle size={12} />,
  },
  not_evaluated: {
    chip: 'text-ink-muted',
    soft: 'bg-surface-sunken',
    icon: <CircleDashed size={12} />,
  },
};

function RowVerdictChip({ row }: { row: GoalRequirementRow }) {
  const tone = VERDICT_TONE[row.verdict] ?? VERDICT_TONE.not_evaluated;
  return (
    <span
      className={`inline-flex shrink-0 items-center gap-1 rounded-pill px-2 py-px text-meta ${tone.chip} ${tone.soft}`}
      data-verdict={row.verdict}
    >
      {tone.icon}
      {row.verdict}
    </span>
  );
}

export function GoalSatisfactionCard({ view }: GoalSatisfactionCardProps) {
  const t = useT();
  const tone = VERDICT_TONE[view.verdict] ?? VERDICT_TONE.not_evaluated;
  const rows = view.requirements.slice(0, 12);
  return (
    <div
      data-testid="goal-satisfaction-card"
      data-verdict={view.verdict}
      role="status"
      aria-label={t('chat.goal.title')}
      className="my-2 rounded-md border border-edge-subtle bg-surface-raised p-3 text-body"
    >
      {/* Header */}
      <div className="flex items-start gap-2">
        <div
          className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-md ${tone.soft} ${tone.chip}`}
        >
          {tone.icon}
        </div>
        <div className="min-w-0 flex-1">
          <span className="text-title font-semibold text-ink">
            {t('chat.goal.title')}
          </span>
          <span
            className={`ml-2 inline-flex items-center gap-1 rounded-pill px-2 py-px text-meta ${tone.chip} ${tone.soft}`}
          >
            {tone.icon} {view.verdict}
          </span>
          <div className="mt-1 text-body text-ink-muted">
            {t('chat.goal.subtitle', { signal: view.signal })}
          </div>
        </div>
      </div>

      {/* Requirement ledger */}
      {rows.length > 0 && (
        <div className="mt-2.5 flex flex-col gap-1.5">
          {rows.map((row, i) => (
            <div
              key={`${row.id}-${i}`}
              data-testid="goal-requirement-row"
              data-requirement-id={row.id}
              className="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md bg-surface-sunken px-2 py-1.5"
            >
              <span className="min-w-0 flex-1 truncate text-body text-ink" title={row.id}>
                {row.id}
              </span>
              <RowVerdictChip row={row} />
              {row.missing.length > 0 && (
                <span
                  className="text-meta text-status-warning"
                  title={row.missing.join(', ')}
                >
                  {t('chat.goal.missing', { codes: row.missing.join(', ') })}
                </span>
              )}
              {row.next_action && (
                <span className="text-meta text-ink-muted">
                  {t('chat.goal.nextAction', { action: row.next_action })}
                </span>
              )}
            </div>
          ))}
        </div>
      )}

      {/* Summary line */}
      {view.summary_line && (
        <div className="mt-2 truncate text-meta text-ink-muted" title={view.summary_line}>
          {view.summary_line}
        </div>
      )}
    </div>
  );
}
