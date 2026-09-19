'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
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
  // 增量修订号：GET 在飞期间只要有事件落过账，快照就不是最新真相。
  const eventRevisionRef = useRef(0);
  const prevSessionIdRef = useRef<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      prevSessionIdRef.current = null;
      setView(EMPTY_SESSION_PLAN_STATE);
      return;
    }
    // 只有「具体会话 → 另一个会话」才算切换；undefined/null → assigned 是
    // 服务端给当前实时流补发 session id（useMapBridge 同款豁免），此时清场
    // 会把本轮已到达的增量抹掉。owner_token 补齐（null → issued）同理。
    const prev = prevSessionIdRef.current;
    prevSessionIdRef.current = sessionId;
    if (prev !== null && prev !== sessionId) {
      setView(EMPTY_SESSION_PLAN_STATE);
    }
    let cancelled = false;
    const revisionAtStart = eventRevisionRef.current;
    getSessionPlan(sessionId, ownerToken)
      .then((p) => {
        if (cancelled) return;
        setView((prevState) => {
          // 水合竞速：请求开始后落过账的事件投影比快照新 —— 保留事件态
          // （快照只补还没有任何投影的空缺）。无事件落账时才用快照覆盖
          // （横幅只在会话切换时清；水合竞速期间到达的 superseded 横幅保留）。
          if (eventRevisionRef.current !== revisionAtStart && prevState.plan !== null) {
            return prevState;
          }
          return { plan: p ?? null, supersede: prevState.supersede };
        });
      })
      .catch(() => {
        if (!cancelled) {
          // 降级：隐藏而非报错；事件已开户的信封不因水合失败被清掉。
          setView((prevState) => (prevState.plan !== null ? prevState : { ...prevState, plan: null }));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [sessionId, ownerToken]);

  const applyEvent = useCallback((eventName: string, data: unknown) => {
    eventRevisionRef.current += 1;
    setView((prev) => applySessionPlanEvent(prev, eventName, data));
  }, []);

  return { view, applySessionPlanEvent: applyEvent };
}
