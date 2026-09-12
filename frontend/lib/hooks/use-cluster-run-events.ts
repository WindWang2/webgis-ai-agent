/**
 * run 事件游标轮询（ADR-0142 D3）—— geocompute 观测事件是 after_id 游标的
 * 轮询 JSON（非 SSE），本 hook 是游标通道专用变体，纪律与 use-cluster-poll
 * 同源但语义不同（累积而非替换）：
 *
 * - 游标推进：每页取回后 after_id=页尾 id，下一页从游标续读（幂等可重放）；
 * - 有界缓冲：事件环形上限 MAX_BUFFERED_EVENTS=500（大屏不因长 run 膨胀）；
 * - 终态即停：出现 run_completed/run_failed/run_cancelled/run_preempted
 *   → 停轮询（bounded，不留空转定时器）；
 * - retention 清理（404）→ 'notfound' 诚实态（「事件不可用」而非静默空表，
 *   对齐 geocompute.ts 的 RunEventsUnavailableError 契约）；
 * - 连错 ≥3 / tab 隐藏暂停 / runId 切换 abort+重置。
 */
'use client';

import { useEffect, useRef, useState } from 'react';
import {
  getRunEvents,
  RunEventsUnavailableError,
  type GeoComputeRunEvent,
} from '@/lib/api/geocompute';
import { MIN_POLL_INTERVAL_MS } from './use-cluster-poll';

export const MAX_BUFFERED_EVENTS = 500;

const TERMINAL_EVENTS = new Set(['run_completed', 'run_failed', 'run_cancelled', 'run_preempted']);

export type RunEventChannel =
  | 'idle'
  | 'polling'
  | 'terminal'
  | 'notfound'
  | 'error'
  | 'paused';

export interface UseClusterRunEventsOptions {
  runId: string | null;
  enabled?: boolean;
  pollIntervalMs?: number;
  ownerToken?: string | null;
  /** 页大小（后端钳 ≤200）。 */
  pageSize?: number;
}

export interface UseClusterRunEventsResult {
  events: GeoComputeRunEvent[];
  /** 游标（最后一条已见事件 id）；0 = 尚未见到任何事件。 */
  cursor: number;
  channel: RunEventChannel;
  error: string | null;
}

export function useClusterRunEvents(
  options: UseClusterRunEventsOptions,
): UseClusterRunEventsResult {
  const {
    runId,
    enabled = true,
    pollIntervalMs = MIN_POLL_INTERVAL_MS,
    ownerToken,
    pageSize = 200,
  } = options;

  const [events, setEvents] = useState<GeoComputeRunEvent[]>([]);
  const [cursor, setCursor] = useState(0);
  const [channel, setChannel] = useState<RunEventChannel>(runId && enabled ? 'polling' : 'idle');
  const [error, setError] = useState<string | null>(null);

  const generationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const cursorRef = useRef(0);
  const errorCountRef = useRef(0);
  const mountedRef = useRef(true);
  const hiddenRef = useRef(false);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    // runId 切换 / 卸载：整代失效 + abort（纪律⑤）。
    generationRef.current += 1;
    abortRef.current?.abort();
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    cursorRef.current = 0;
    errorCountRef.current = 0;
    setEvents([]);
    setCursor(0);
    setError(null);
    setChannel(runId && enabled ? 'polling' : 'idle');
    if (!runId || !enabled) return;

    const generation = generationRef.current;
    let stopped = false;

    const schedule = (delay: number) => {
      if (stopped || !mountedRef.current || generation !== generationRef.current) return;
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        void tick();
      }, delay);
    };

    const tick = async () => {
      if (stopped || !mountedRef.current || generation !== generationRef.current) return;
      if (hiddenRef.current) return; // 隐藏 → 暂停（可见性 effect 负责补拉重启）
      if (errorCountRef.current >= 3) {
        setChannel('error');
        return; // 有界重试：不再排下一次
      }
      // 快速连续触发（如 visibility 抖动）时废弃在飞请求，防止同页双取重复入缓冲
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;
      try {
        const page = await getRunEvents(runId, {
          afterId: cursorRef.current,
          limit: pageSize,
          ownerToken,
          signal: controller.signal,
        });
        if (stopped || generation !== generationRef.current || !mountedRef.current) return;
        errorCountRef.current = 0;
        setError(null);
        if (page.events.length > 0) {
          cursorRef.current = page.after_id;
          setCursor(page.after_id);
          setEvents((prev) => {
            const merged = [...prev, ...page.events];
            return merged.length > MAX_BUFFERED_EVENTS
              ? merged.slice(merged.length - MAX_BUFFERED_EVENTS)
              : merged;
          });
          if (page.events.some((e) => TERMINAL_EVENTS.has(e.event))) {
            setChannel('terminal');
            return; // 终态即停：不排下一次
          }
        }
        setChannel('polling');
        schedule(pollIntervalMs);
      } catch (err) {
        if (stopped || generation !== generationRef.current || !mountedRef.current) return;
        if (controller.signal.aborted) return;
        if (err instanceof RunEventsUnavailableError) {
          // retention 清理（not_found）=「事件不可用」诚实态，不是静默空表
          setChannel(err.reason === 'not_found' ? 'notfound' : 'error');
          setError(err.reason === 'not_found' ? '事件已随 run 行清理，不可用' : '集群暂不可用');
          return;
        }
        errorCountRef.current += 1;
        setError(err instanceof Error && err.message ? err.message : '事件拉取失败');
        schedule(pollIntervalMs);
      }
    };

    void tick();

    const onVisibility = () => {
      hiddenRef.current = document.hidden;
      if (!document.hidden) {
        // 恢复可见：清零错误计数并立即补一页（纪律②）
        errorCountRef.current = 0;
        if (timerRef.current !== null) {
          clearTimeout(timerRef.current);
          timerRef.current = null;
        }
        void tick();
      }
    };
    hiddenRef.current = typeof document !== 'undefined' ? document.hidden : false;
    document.addEventListener('visibilitychange', onVisibility);

    return () => {
      stopped = true;
      document.removeEventListener('visibilitychange', onVisibility);
      if (timerRef.current !== null) {
        clearTimeout(timerRef.current);
        timerRef.current = null;
      }
      abortRef.current?.abort();
    };
  }, [runId, enabled, pollIntervalMs, ownerToken, pageSize]);

  return { events, cursor, channel, error };
}
