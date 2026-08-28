'use client';

import { useEffect, useState } from 'react';
import { ClipboardList, Check, Circle, MinusCircle, CircleSlash } from 'lucide-react';
import type { ReactElement } from 'react';
import { getSessionPlan } from '@/lib/api/chat';
import type {
  SessionPlanCapabilityStatus,
  SessionPlanProjection,
} from '@/lib/types/session-plan';

interface Props {
  sessionId?: string | null;
  ownerToken?: string | null;
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
};

/**
 * SessionPlan 面板（Pi 路径，#1047）——挂载在 chat 侧边栏的会话级计划卡。
 *
 * 只读水合：mount / sessionId 变化时拉取当前信封投影；SSE 增量（#1048）
 * 不在本组件。降级约定：端点失败 → 面板隐藏，绝不阻塞聊天面；无信封 →
 * 整卡隐藏（ChatEngine 兜底会话与今天完全一致）；槽位已开但 GIS 章节为
 * 空 → 显式「暂无计划内容」空态，不是空白卡。
 */
export function SessionPlanPanel({ sessionId, ownerToken }: Props) {
  const [plan, setPlan] = useState<SessionPlanProjection | undefined>(undefined);

  useEffect(() => {
    if (!sessionId) {
      setPlan(undefined);
      return;
    }
    let cancelled = false;
    // 会话切换先清空：陈旧会话的信封绝不涂到新会话的侧边栏上。
    setPlan(undefined);
    getSessionPlan(sessionId, ownerToken)
      .then((p) => {
        if (!cancelled) setPlan(p);
      })
      .catch(() => {
        if (!cancelled) setPlan(undefined); // 降级：隐藏而非报错
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, ownerToken]);

  if (!plan) return null;

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
