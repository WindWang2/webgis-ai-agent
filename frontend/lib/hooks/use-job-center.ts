/**
 * 任务中心数据源（ADR-0052，规范 §27 / §31 / §32）。
 *
 * 设计约束：
 *   * **不引入新的 websocket 系统**，也不做高频轮询。SSE 仍然是主推送通道；
 *     这里只提供「浏览器刷新 / 重连后恢复后台 job」所需的轻量兜底轮询。
 *   * 轮询生命周期严格有界：
 *       - 没有活跃 job → 完全停止（0 请求），只在用户操作或会话切换时再拉一次；
 *       - tab 隐藏 → 暂停，重新可见时立刻补一次；
 *       - 组件卸载 / session 切换 → abort 在飞请求；
 *       - 连续失败达到上限 → 停止轮询并暴露错误，不无限重试打后端。
 *   * 陈旧响应保护：每次请求带一个自增 requestId 与当时的 sessionId，回来时若
 *     两者与当前状态不符则整份丢弃 —— 旧 session 的任务绝不会写进新 session 的
 *     UI（规范 Scenario 10）。
 */
'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { cancelJob, listJobs, retryJob, type JobView } from '@/lib/api/jobs';
import { ApiError } from '@/lib/api/transport';

/** 有活跃 job 时的轮询间隔。后端也会通过 poll_after_ms 建议，取两者较大值。 */
export const DEFAULT_POLL_INTERVAL_MS = 3000;
/** tab 隐藏时不轮询；重新可见时立即补一次。 */
export const MAX_CONSECUTIVE_ERRORS = 3;

export interface UseJobCenterOptions {
  sessionId?: string | null;
  ownerToken?: string | null;
  /** 关闭轮询（面板收起时传 false，避免看不见的面板还在打后端） */
  enabled?: boolean;
  pollIntervalMs?: number;
}

export interface UseJobCenterResult {
  jobs: JobView[];
  loading: boolean;
  error: string | null;
  hasActive: boolean;
  /** 已提交取消请求、后端尚未确认终态的 job id（UI 显示「取消中…」） */
  cancelling: Set<string>;
  refresh: () => Promise<void>;
  cancel: (jobId: string) => Promise<void>;
  retry: (jobId: string) => Promise<void>;
}

function errorMessage(err: unknown): string {
  if (err instanceof Error && err.message) return err.message;
  return '任务中心请求失败';
}

export function useJobCenter(options: UseJobCenterOptions = {}): UseJobCenterResult {
  const { sessionId = null, ownerToken = null, enabled = true } = options;
  const pollIntervalMs = options.pollIntervalMs ?? DEFAULT_POLL_INTERVAL_MS;

  const [jobs, setJobs] = useState<JobView[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState<Set<string>>(new Set());
  // 每次拉取尝试后自增，驱动「排下一次轮询」的调度 effect。
  // 用它而不是直接依赖 jobs：拉取失败时 jobs 不变，但仍需要（有界地）重排。
  const [pollTick, setPollTick] = useState(0);

  // 请求代次 + 当时的 session：用于丢弃陈旧响应
  const generationRef = useRef(0);
  // 会话轮次：**只**在 session 切换或卸载时递增。cancel/retry 的守卫必须用它而不是
  // generationRef —— 后者每次拉取都自增，会把「刚刚由本次操作触发的刷新」误判成陈旧。
  const epochRef = useRef(0);
  const sessionRef = useRef<string | null>(sessionId);
  const abortRef = useRef<AbortController | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const errorCountRef = useRef(0);
  const mountedRef = useRef(true);
  // 后端建议的下次间隔（poll_after_ms）
  const serverIntervalRef = useRef<number | null>(null);

  const hasActive = useMemo(() => jobs.some((job) => job.active), [jobs]);

  const clearTimer = useCallback(() => {
    if (timerRef.current !== null) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const fetchOnce = useCallback(async () => {
    if (!enabled) return;
    // 没有 session 就没有归属证明，后端会返回空列表 —— 干脆不发请求
    if (!sessionId) {
      setJobs([]);
      return;
    }

    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const generation = ++generationRef.current;
    const requestSession = sessionId;
    setLoading(true);
    try {
      const data = await listJobs({
        sessionId: requestSession,
        ownerToken,
        signal: controller.signal,
      });
      // 陈旧响应保护：代次落后，或 session 已切换 → 整份丢弃
      if (generation !== generationRef.current) return;
      if (sessionRef.current !== requestSession) return;
      if (!mountedRef.current) return;

      setJobs(data.jobs ?? []);
      serverIntervalRef.current = data.poll_after_ms ?? null;
      setError(null);
      errorCountRef.current = 0;

      // 后端已确认终态 → 从「取消中」集合里移除
      setCancelling((prev) => {
        if (prev.size === 0) return prev;
        const next = new Set(prev);
        for (const job of data.jobs ?? []) {
          if (!job.active) next.delete(job.id);
        }
        return next.size === prev.size ? prev : next;
      });
    } catch (err) {
      if (controller.signal.aborted) return; // 主动取消不算错误
      if (generation !== generationRef.current || !mountedRef.current) return;
      // 404 = 调用方对这个 session 无归属证明（典型场景：刷新后恢复匿名会话，
      // ownerToken 还没从后端取回）。这是「没有可见任务」而不是故障 —— 显示成
      // 红色错误横幅只会让用户困惑。
      if (err instanceof ApiError && err.status === 404) {
        setJobs([]);
        setError(null);
        errorCountRef.current = 0;
        return;
      }
      errorCountRef.current += 1;
      setError(errorMessage(err));
    } finally {
      if (generation === generationRef.current && mountedRef.current) {
        setLoading(false);
        setPollTick((tick) => tick + 1);
      }
    }
  }, [enabled, sessionId, ownerToken]);

  // session 切换：立刻丢弃旧数据，避免旧 session 的任务短暂显示在新 session 下
  useEffect(() => {
    sessionRef.current = sessionId;
    generationRef.current += 1;
    epochRef.current += 1;
    abortRef.current?.abort();
    setJobs([]);
    setCancelling(new Set());
    setError(null);
    errorCountRef.current = 0;
  }, [sessionId]);

  // 首次挂载 / 依赖变化时拉一次。浏览器刷新后任务中心据此恢复。
  // 注意这个 effect 不依赖 jobs —— 否则每次拉取结果变化都会立刻再拉一次。
  useEffect(() => {
    mountedRef.current = true;
    if (!enabled || !sessionId) return;
    void fetchOnce();
  }, [enabled, sessionId, ownerToken, fetchOnce]);

  // 轮询调度：只**排下一次**，自身从不立即请求。
  // 停止条件（规范 §32）：无活跃 job / tab 隐藏 / 连续失败超上限 / 无 session。
  useEffect(() => {
    if (!enabled || !sessionId) return;
    if (!hasActive) return;                                  // 无活跃 job → 0 轮询
    if (errorCountRef.current >= MAX_CONSECUTIVE_ERRORS) return;  // 有界重试
    if (typeof document !== 'undefined' && document.hidden) return;  // 隐藏 → 暂停

    const interval = Math.max(pollIntervalMs, serverIntervalRef.current ?? 0);
    timerRef.current = setTimeout(() => {
      void fetchOnce();
    }, interval);
    return clearTimer;
  }, [enabled, sessionId, hasActive, pollIntervalMs, pollTick, fetchOnce, clearTimer]);

  // tab 重新可见：给一次重试机会并立刻补一次（补拉会 bump pollTick 重启调度）
  useEffect(() => {
    if (typeof document === 'undefined') return;
    const onVisibility = () => {
      if (document.hidden) {
        clearTimer();
        return;
      }
      if (!enabled || !sessionId) return;
      errorCountRef.current = 0;
      void fetchOnce();
    };
    document.addEventListener('visibilitychange', onVisibility);
    return () => document.removeEventListener('visibilitychange', onVisibility);
  }, [enabled, sessionId, fetchOnce, clearTimer]);

  useEffect(
    () => () => {
      mountedRef.current = false;
      clearTimer();
      abortRef.current?.abort();
    },
    [clearTimer],
  );

  const cancel = useCallback(
    async (jobId: string) => {
      // 卸载/切换 session 后到达的响应不得再写 UI（与 fetchOnce 同样的守卫）
      const epoch = epochRef.current;
      const isStale = () => !mountedRef.current || epoch !== epochRef.current;

      // 乐观进入「取消中」—— 但绝不直接显示「已取消」：只有后端到达终态才算
      setCancelling((prev) => new Set(prev).add(jobId));
      try {
        const res = await cancelJob(jobId, { ownerToken });
        if (isStale()) return;
        setJobs((prev) =>
          prev.map((job) => (job.id === jobId ? { ...job, status: res.status, active: res.cancelling || job.active } : job)),
        );
        if (!res.cancelling) {
          // 后端已终态（或本就终态）→ 退出「取消中」显示
          setCancelling((prev) => {
            const next = new Set(prev);
            next.delete(jobId);
            return next;
          });
        }
        await fetchOnce();
      } catch (err) {
        if (isStale()) return;
        // 取消失败：回滚乐观状态并暴露错误（规范 §30）
        setCancelling((prev) => {
          const next = new Set(prev);
          next.delete(jobId);
          return next;
        });
        setError(errorMessage(err));
      }
    },
    [ownerToken, fetchOnce],
  );

  const retry = useCallback(
    async (jobId: string) => {
      const epoch = epochRef.current;
      const isStale = () => !mountedRef.current || epoch !== epochRef.current;
      try {
        const res = await retryJob(jobId, { ownerToken });
        if (isStale()) return;
        errorCountRef.current = 0;
        // 先刷新再写错误：fetchOnce 成功时会清空 error，若顺序颠倒，
        // 「无法重试」的原因会被立刻擦掉，用户看不到为什么被拒绝。
        await fetchOnce();
        if (isStale()) return;
        if (!res.retried) setError(`无法重试：${res.reason}`);
      } catch (err) {
        if (isStale()) return;
        setError(errorMessage(err));
      }
    },
    [ownerToken, fetchOnce],
  );

  return {
    jobs,
    loading,
    error,
    hasActive,
    cancelling,
    refresh: fetchOnce,
    cancel,
    retry,
  };
}
