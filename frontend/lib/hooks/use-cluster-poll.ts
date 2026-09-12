/**
 * 有界轮询内核（ADR-0142 D3）—— use-job-center 六条纪律在 cluster 观测面的
 * 逐条复刻：
 *   ① enabled=false → 0 请求；
 *   ② tab 隐藏 → 暂停，重新可见 → 立即补拉并清零错误计数；
 *   ③ 连续失败 ≥ MAX_CONSECUTIVE_ERRORS → 停止轮询并暴露错误（不无限重试）；
 *   ④ generation 守卫丢弃陈旧响应（resetKey 切换时整代失效）；
 *   ⑤ 卸载 / resetKey 切换 → abort 在飞请求；
 *   ⑥ 轮询间隔下限 MIN_POLL_INTERVAL_MS=3000（后端限流 240 req/min 预算）。
 *
 * 与 use-job-center 的差异：cluster 观测面没有「无活跃 job → 完全停」语义
 * （面板打开就该持续看板），代之以 enabled（面板收起即停）+ 可选
 * pollWhile 断言（如「存在未完成 run」时才高频轮）。
 */
'use client';

import { useCallback, useEffect, useRef, useState } from 'react';

export const MIN_POLL_INTERVAL_MS = 3000;
export const DEFAULT_CLUSTER_POLL_INTERVAL_MS = 5000;
export const MAX_CONSECUTIVE_ERRORS = 3;

export interface BoundedPollStatus {
  /** 最近一次成功接收时间（ISO）；null = 从未成功。 */
  lastFetchedAt: string | null;
  /** 当前连续失败计数（数据通道自检卡消费）。 */
  consecutiveErrors: number;
  /** 轮询是否处于暂停（隐藏 / 错误上限 / enabled=false）。 */
  paused: boolean;
  pauseReason: 'hidden' | 'error-limit' | 'disabled' | null;
}

export interface UseBoundedPollOptions<T> {
  /** 拉取函数；收到内部 abort signal。identity 变化会触发重新拉取。 */
  fetcher: (signal: AbortSignal) => Promise<T>;
  /** 总开关（面板收起传 false）。默认 true。 */
  enabled?: boolean;
  pollIntervalMs?: number;
  /** 复位键（如 runId / namespace）：变化即清数据、abort、重新拉取。 */
  resetKey?: string;
}

export interface UseBoundedPollResult<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  /** 最近一次原始错误对象（typed 分类用，如 GeoComputeApiError）。 */
  lastError: unknown;
  status: BoundedPollStatus;
  /** 立即拉一次（用户刷新按钮）；不计入轮询调度。 */
  refresh: () => void;
}

function describeError(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return '请求失败';
}

export function useBoundedPoll<T>(options: UseBoundedPollOptions<T>): UseBoundedPollResult<T> {
  const { enabled = true, resetKey } = options;
  const pollIntervalMs = Math.max(
    options.pollIntervalMs ?? DEFAULT_CLUSTER_POLL_INTERVAL_MS,
    MIN_POLL_INTERVAL_MS,
  );
  const fetcher = options.fetcher;

  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastError, setLastError] = useState<unknown>(null);
  const [pollTick, setPollTick] = useState(0);
  const [lastFetchedAt, setLastFetchedAt] = useState<string | null>(null);
  const [consecutiveErrors, setConsecutiveErrors] = useState(0);
  const [hidden, setHidden] = useState(false);

  const generationRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const errorCountRef = useRef(0);
  const mountedRef = useRef(true);
  // fetcher 经 ref 间接调用：调用方传内联闭包也不会重启轮询调度。
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const fetchNow = useCallback(() => {
    if (!enabled || !mountedRef.current) return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const generation = ++generationRef.current;
    setLoading(true);
    fetcherRef
      .current(controller.signal)
      .then((next) => {
        if (generation !== generationRef.current || !mountedRef.current) return;
        setData(next);
        setError(null);
        setLastError(null);
        errorCountRef.current = 0;
        setConsecutiveErrors(0);
        setLastFetchedAt(new Date().toISOString());
      })
      .catch((err: unknown) => {
        if (controller.signal.aborted) return; // 主动取消不算错误
        if (generation !== generationRef.current || !mountedRef.current) return;
        errorCountRef.current += 1;
        setConsecutiveErrors(errorCountRef.current);
        setLastError(err);
        setError(describeError(err));
      })
      .finally(() => {
        if (generation !== generationRef.current || !mountedRef.current) return;
        setLoading(false);
        setPollTick((tick) => tick + 1); // 驱动调度 effect 重排下一次
      });
  }, [enabled]);

  // resetKey 变化：旧数据立即失效（陈旧 namespace 的数据绝不留在新面板下）。
  useEffect(() => {
    generationRef.current += 1;
    abortRef.current?.abort();
    setData(null);
    setError(null);
    setLastError(null);
    errorCountRef.current = 0;
    setConsecutiveErrors(0);
    setLastFetchedAt(null);
    if (enabled && mountedRef.current) fetchNow();
  }, [resetKey, enabled, fetchNow]);

  // 首拉与手动 refresh 共用：不进调度循环。
  const refresh = useCallback(() => {
    if (enabled) fetchNow();
  }, [enabled, fetchNow]);

  // 调度：只排下一次。停止条件：disabled / hidden / 错误上限。
  useEffect(() => {
    if (!enabled) return;
    if (typeof document !== 'undefined' && document.hidden) {
      setHidden(true);
      clearTimer();
      return;
    }
    setHidden(false);
    if (errorCountRef.current >= MAX_CONSECUTIVE_ERRORS) return; // 有界重试
    timerRef.current = setTimeout(() => fetchNow(), pollIntervalMs);
    return clearTimer;
  }, [enabled, pollIntervalMs, pollTick, fetchNow, clearTimer]);

  // 可见性：隐藏暂停；恢复可见立即补拉 + 清零错误计数。
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const onVisibility = () => {
      if (document.hidden) {
        setHidden(true);
        clearTimer();
        return;
      }
      setHidden(false);
      if (!enabled) return;
      errorCountRef.current = 0;
      setConsecutiveErrors(0);
      fetchNow();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [enabled, fetchNow, clearTimer]);

  // 卸载清理。
  useEffect(
    () => () => {
      mountedRef.current = false;
      clearTimer();
      abortRef.current?.abort();
    },
    [clearTimer],
  );

  const paused = !enabled || hidden || consecutiveErrors >= MAX_CONSECUTIVE_ERRORS;
  const pauseReason: BoundedPollStatus['pauseReason'] = !enabled
    ? 'disabled'
    : hidden
      ? 'hidden'
      : consecutiveErrors >= MAX_CONSECUTIVE_ERRORS
        ? 'error-limit'
        : null;

  return {
    data,
    loading,
    error,
    lastError,
    status: { lastFetchedAt, consecutiveErrors, paused, pauseReason },
    refresh,
  };
}
