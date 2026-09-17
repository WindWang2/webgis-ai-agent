'use client';

/**
 * useCockpitProjection — cockpit 投影接数内核（传输无关）。
 *
 * 在 useBoundedPoll 的六条纪律之上补两条 cockpit 特有守卫：
 *  - revision 单调：迟到旧响应（revision 更小）直接丢弃，视图保持在
 *    更新版本；同 revision 幂等应用。裁决来源优先 revisionOf selector
 *    （mission.revision），缺省回退信封 generated_at。
 *  - 服务端节奏自适应：信封 poll_after_ms 钳制后作为下一轮间隔；
 *    null/缺失回退默认。前端不自造节奏。
 *
 * session 切换/卸载/隐藏暂停/错误上限/abort 全部沿用 useBoundedPoll。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  useBoundedPoll,
  type UseBoundedPollResult,
} from '@/lib/hooks/use-cluster-poll';
import { clampPollAfterMs } from '@/lib/api/cockpit';

export const DEFAULT_COCKPIT_POLL_MS = 5000;

/** 投影 revision 提取：selector 缺失时回退信封 generated_at。 */
export function projectionRevision(
  data: unknown,
  revisionOf?: (d: never) => number,
): number {
  if (revisionOf) {
    const v = (revisionOf as unknown as (d: unknown) => number)(data);
    return typeof v === 'number' && Number.isFinite(v) ? v : 0;
  }
  const v = (data as { generated_at?: unknown } | null)?.generated_at;
  return typeof v === 'number' && Number.isFinite(v) ? v : 0;
}

export interface UseCockpitProjectionOptions<T> {
  /** 面板不可见 / 未选 mission 时传 false（0 请求纪律）。 */
  enabled?: boolean;
  /** 复位键（sessionId / missionId）：变化即清数据 + abort 在飞。 */
  resetKey?: string;
  /** revision 提取器（如 mission.revision）；缺省用信封 generated_at。 */
  revisionOf?: (data: T) => number;
  /** 轮询间隔下限（也是 poll_after_ms 缺失时的回退值）。 */
  pollIntervalMs?: number;
}

export interface UseCockpitProjectionResult<T> extends UseBoundedPollResult<T> {
  /** 当前生效的轮询间隔（服务端建议钳制后）。 */
  appliedPollMs: number;
}

export function useCockpitProjection<T>(
  fetcher: (signal: AbortSignal) => Promise<T>,
  options: UseCockpitProjectionOptions<T> = {},
): UseCockpitProjectionResult<T> {
  const { enabled = true, resetKey, revisionOf, pollIntervalMs = DEFAULT_COCKPIT_POLL_MS } = options;
  const [appliedPollMs, setAppliedPollMs] = useState(
    Math.max(pollIntervalMs, 3000),
  );
  // 已应用的最大 revision：0 = 尚无数据。
  const lastRevisionRef = useRef(0);
  const lastDataRef = useRef<T | null>(null);
  const revisionOfRef = useRef(revisionOf);
  revisionOfRef.current = revisionOf;

  // resetKey（mission/session）切换：守卫基线一并失效。声明在
  // useBoundedPoll 之前 —— 同一次 commit 里先于其 reset/fetch effect 执行，
  // 旧目标的高 revision 绝不套在新目标的低 revision 响应上（review P1-1）。
  useEffect(() => {
    lastRevisionRef.current = 0;
    lastDataRef.current = null;
  }, [resetKey]);

  const guardedFetcher = useCallback(
    async (signal: AbortSignal): Promise<T> => {
      const next = await fetcher(signal);
      if (signal.aborted) return next; // 已失效，上层会丢弃
      const rev = projectionRevision(next, revisionOfRef.current as never);
      if (rev < lastRevisionRef.current && lastDataRef.current !== null) {
        // 迟到的旧投影：保持当前视图（返回旧对象即状态不变）。
        return lastDataRef.current;
      }
      lastRevisionRef.current = Math.max(lastRevisionRef.current, rev);
      lastDataRef.current = next;
      const envelope = next as { poll_after_ms?: number | null };
      setAppliedPollMs(
        clampPollAfterMs(envelope?.poll_after_ms, Math.max(pollIntervalMs, 3000)),
      );
      return next;
    },
    [fetcher, pollIntervalMs],
  );

  const poll = useBoundedPoll<T>({
    fetcher: guardedFetcher,
    enabled,
    pollIntervalMs: appliedPollMs,
    resetKey,
  });
  return { ...poll, appliedPollMs };
}
