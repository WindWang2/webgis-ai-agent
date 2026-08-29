'use client';

import { useEffect, useState } from 'react';
import { ClipboardList, Check, Circle, MinusCircle, CircleSlash, RotateCcw } from 'lucide-react';
import type { ReactElement } from 'react';
import { getSessionPlan } from '@/lib/api/chat';
import type {
  SessionPlanCapabilityStatus,
  SessionPlanProjection,
} from '@/lib/types/session-plan';
import type { SessionPlanViewState } from '@/lib/session/session-plan-delta';

interface Props {
  sessionId?: string | null;
  ownerToken?: string | null;
  /**
   * #1048：流式增量驱动的实时状态（useSessionPlan 的 view）。提供时面板由它
   * 渲染且不做自身水合（避免双重 GET）；未提供（既有调用方/测试）时保持
   * #1047 的 mount 水合行为。
   */
  live?: SessionPlanViewState;
}

// 已作废（目标变更被作废）与不可用（无法满足）观感必须可区分——前者是
// 中性灰，后者是警示色；完成走 success 绿，待完成沿用 plan-card 的脉冲点。
// label 与 icon 同源一份 map，新增状态时不会漂移。
const STATUS_META: Record<
  SessionPlanCapabilityStatus,
  { label: string; icon: ReactElement }
> = {
  pending: {
    label: '待完成',
    icon: <Circle className="h-3 w-3 text-ink-disabled animate-pulse" />,
  },
  complete: {
    label: '已完成',
    icon: <Check className="h-3 w-3 text-status-success" />,
  },
  voided: {
    label: '已作废',
    icon: <MinusCircle className="h-3 w-3 text-ink-disabled" />,
  },
  unavailable: {
    label: '不可用',
    icon: <CircleSlash className="h-3 w-3 text-status-warning" />,
  },
  failed: {
    // v3(Phase E)：执行过但未产出 artifact —— 与 unavailable（裁决期就
    // 不可用）区分：failed 可重试，重试成功覆写 complete。
    label: '失败（可重试）',
    icon: <RotateCcw className="h-3 w-3 text-status-warning" />,
  },
};

/**
 * SessionPlan 面板（Pi 路径，#1047）——挂载在 chat 侧边栏的会话级计划卡。
 *
 * 状态来源二选一：`live`（#1048，useSessionPlan 的 hydrate-then-delta，
 * 流式增量实时驱动）或自身 mount 水合（#1047 行为保留）。降级约定：端点
 * 失败 → 面板隐藏，绝不阻塞聊天面；无信封 → 整卡隐藏（ChatEngine 兜底
 * 会话与今天完全一致）；槽位已开但 GIS 章节为空 → 显式「暂无计划内容」
 * 空态，不是空白卡。superseded 交接（previous goal → new goal）由增量层
 * 携带，GET 投影刻意不含 previous_goal。
 */
export function SessionPlanPanel({ sessionId, ownerToken, live }: Props) {
  const [hydrated, setHydrated] = useState<SessionPlanProjection | undefined>(undefined);
  const liveProvided = live !== undefined;

  useEffect(() => {
    if (liveProvided) return; // 流式状态接管，绝不做自身水合（避免双重 GET）
    if (!sessionId) {
      setHydrated(undefined);
      return;
    }
    let cancelled = false;
    // 会话切换先清空：陈旧会话的信封绝不涂到新会话的侧边栏上。
    setHydrated(undefined);
    getSessionPlan(sessionId, ownerToken)
      .then((p) => {
        if (!cancelled) setHydrated(p);
      })
      .catch(() => {
        if (!cancelled) setHydrated(undefined); // 降级：隐藏而非报错
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, ownerToken, liveProvided]);

  const view: SessionPlanViewState = live ?? { plan: hydrated ?? null, supersede: null };
  if (!view.plan) return null;
  const plan = view.plan;

  const hasChapter = plan.query !== null || plan.recipe_id !== null;

  return (
    <div
      data-testid="session-plan-panel"
      className="mx-panel mt-2 p-3 rounded-md border border-edge-subtle bg-surface-raised"
    >
      <div className="flex items-center gap-2 mb-2">
        <ClipboardList className="h-4 w-4 text-status-accent" />
        <span className="text-body font-semibold text-ink">会话计划</span>
      </div>
      {view.supersede && (
        <div
          data-testid="session-plan-superseded"
          className="mb-2 rounded-sm bg-status-accent-soft px-2 py-1 text-micro text-ink"
        >
          目标已更换：{view.supersede.previous_goal} → {view.supersede.goal}
        </div>
      )}
      {hasChapter ? (
        <>
          <div className="space-y-1 mb-2">
            <div className="flex items-start gap-2 text-body">
              <span className="shrink-0 text-ink-muted">目标</span>
              <span className="text-ink truncate">{plan.user_goal || plan.query}</span>
            </div>
            <div className="flex items-start gap-2 text-body">
              <span className="shrink-0 text-ink-muted">配方</span>
              <span className="text-ink-muted truncate">{plan.recipe_id}</span>
            </div>
          </div>
          <div className="text-meta uppercase tracking-wider text-ink-muted mb-1">能力</div>
          <ul className="space-y-1">
            {plan.progress.map((row) => (
              <li key={row.capability} className="flex items-center gap-2 text-body">
                <span className="shrink-0">{STATUS_META[row.status].icon}</span>
                <span
                  className={`flex-1 truncate ${
                    row.status === 'complete' ? 'text-ink' : 'text-ink-muted'
                  }`}
                >
                  {row.capability}
                  {row.status === 'complete' && row.bound_ref && (
                    <span className="ml-1 text-micro text-ink-muted">{row.bound_ref}</span>
                  )}
                </span>
                <span className="shrink-0 text-micro text-ink-muted">
                  {STATUS_META[row.status].label}
                </span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p data-testid="session-plan-empty" className="text-body text-ink-muted">
          暂无计划内容
        </p>
      )}
    </div>
  );
}
