'use client';

import { useCallback, useEffect, useState } from 'react';
import { getSessionPlan } from '@/lib/api/chat';
import {
  applySessionPlanEvent,
  EMPTY_SESSION_PLAN_STATE,
  type SessionPlanViewState,
} from '@/lib/session/session-plan-delta';

/**
 * #1048：SessionPlan 的 hydrate-then-delta 状态钩子（状态从面板组件上提，
 * 由流式事件驱动 —— agentRuntime 同款路径：page.tsx 实例化，经 ContextPanel
 * → ChatTab 以 props 下行到 SessionPlanPanel）。
 *
 * - mount / sessionId 变化：GET 当前信封投影水合（#1047 行为保留 —— 刷新
 *   仍见当前计划）；失败降级为空态（面板隐藏），绝不阻塞聊天面。
 * - applySessionPlanEvent：流 hook 分发链（INV-2 会话守卫之后）转交的
 *   session_plan_* 增量经纯 reducer 叠加；信封关联规则见 session-plan-delta。
 *
 * applySessionPlanEvent 恒稳（空依赖 useCallback）：它是 useSSEStream 新参
 * 的实参，onEvent 的 useCallback 依赖它 —— 身份抖动会打断在飞流。
 */
export function useSessionPlan(
  sessionId: string | null | undefined,
  ownerToken: string | null | undefined,
) {
  const [view, setView] = useState<SessionPlanViewState>(EMPTY_SESSION_PLAN_STATE);

  useEffect(() => {
    if (!sessionId) {
      setView(EMPTY_SESSION_PLAN_STATE);
      return;
    }
    let cancelled = false;
    // 会话切换先清场：陈旧会话的信封/横幅绝不涂到新会话的侧边栏上。
    setView(EMPTY_SESSION_PLAN_STATE);
    getSessionPlan(sessionId, ownerToken)
      .then((p) => {
        if (!cancelled) {
          // 横幅只在会话切换时清；水合竞速期间到达的 superseded 横幅保留。
          setView((prev) => ({ plan: p ?? null, supersede: prev.supersede }));
        }
      })
      .catch(() => {
        if (!cancelled) setView((prev) => ({ ...prev, plan: null })); // 降级：隐藏而非报错
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, ownerToken]);

  const applyEvent = useCallback((eventName: string, data: unknown) => {
    setView((prev) => applySessionPlanEvent(prev, eventName, data));
  }, []);

  return { view, applySessionPlanEvent: applyEvent };
}
