'use client';
import { useCallback, useEffect, useRef } from 'react';
import type { MapSpecRuntime } from '@/lib/mapspec-runtime';
import {
  collectRenderObservation,
  RuntimeErrorRing,
  waitForRenderSettle,
} from '@/lib/mapspec-runtime/render-observation';
import {
  commitMapSpecDocument,
  getMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import { apiFetch, ApiTimeoutError } from '@/lib/api/transport';
import { devOnly } from '@/lib/utils/logger';
import type { Layer } from '@/lib/types/layer';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import type { MapActionPayload } from '@/lib/types';

// Cap on remembered applied repair action_ids (bounded memory; older ids are
// stale generations that the generation gate already drops, so eviction is safe).
const MAX_APPLIED_REPAIR_IDS = 16;
// MAX_REPAIR_ATTEMPTS（FE-P2-3）：单会话客户端派发修复的总预算。
const MAX_TOTAL_SESSION_REPAIRS = 8;

interface UseCartographicObservationOptions {
  /** 共享的 MapSpecRuntime ref（读取 getLastError/getAppliedSpec 作观测证据）。 */
  runtimeRef: React.RefObject<MapSpecRuntime | null>;
  sessionId?: string | null;
  ownerToken?: string | null;
  dispatchAction: (action: MapActionPayload) => void;
  /**
   * P9：访问底层 MapLibre 实例以注册 runtime error 监听（mount→cleanup 生命周期
   * 归本 hook）。缺省时不注册 —— 旧调用方/测试保持原行为。
   */
  getMap?: () => { on: (ev: string, h: (e: unknown) => void) => void; off: (ev: string, h: (e: unknown) => void) => void } | null;
}

/**
 * 制图观测→修复回路（单职责：reconcile 落定后采集一次运行时观测并按
 * generation 安全规则派发修复）。包含：clientGeneration 单调门、
 * AbortController 超越取消、修复 action_id 去重环（上限 16）、以及
 * unmounted / 会话切换 / 更新 generation / 过期指纹 / 重复修复五重
 * 响应守卫。返回 issueCartographicObservation —— 由 reconcile 的
 * .then() 以当次闭包的 map/spec/layers 调用（闭包捕获即原始语义：
 * 迟到响应与最新签发值比较后被丢弃）。
 *
 * P9 render-observed product closure：观测升级为 RenderObservation ——
 * 在既有层收敛证据上补充 mapspec_revision（后端盖章为准）、bounded
 * settle（map idle race 超时）、chrome 组件观察与有界 runtime error 环，
 * 供后端 Map Product Finalizer 做渲染级完成度校验。观测仍是观察而非
 * 地图真相：MapSpec 保持唯一 desired-state 权威。
 */
export function useCartographicObservation({
  runtimeRef,
  sessionId,
  ownerToken,
  dispatchAction,
  getMap,
}: UseCartographicObservationOptions) {
  const lastCartographicObservationKeyRef = useRef<string>('')
  const cartographicObservationGenerationRef = useRef(Date.now() * 1000)
  const cartographicSessionIdRef = useRef(sessionId)
  cartographicSessionIdRef.current = sessionId
  // Cartographic observation→repair generation safety (latest-generation-wins).
  // The backend already rejects stale observations server-side and strips
  // repair_action from stale responses, but HTTP responses within ONE session
  // can still arrive out of order. These refs make the client self-protecting:
  // only the newest issued generation/fingerprint may dispatch a repair.
  const latestIssuedCartographicFingerprintRef = useRef<string>('')
  // Bounded ring of recently-applied repair action_ids so a re-echoed (duplicate)
  // response is applied at most once. Keyed on action_id ONLY — patch_fingerprint
  // can legitimately recur for a different mapspec fingerprint and would wrongly
  // block a valid re-issue, so it is intentionally not used as a dedup key.
  const appliedRepairIdsRef = useRef<Set<string>>(new Set())
  // FE-P2-3：每会话修复总预算熔断——去重环（16）淘汰后旧 action_id 可重新
  // 派发；若修复每轮都改变观测（A↔B 震荡 / 后端持续换新 action_id），回路
  // 理论无界（每轮一个网络往返 + dispatch + reconcile + 观测采集）。超限后
  // 停止派发（观测照常上报），告警一次。会话切换随 sessionId 重置。
  const totalRepairsRef = useRef(0)
  const repairBudgetExhaustedWarnedRef = useRef(false)
  const mountedRef = useRef(true)
  const observationAbortRef = useRef<AbortController | null>(null)
  // P9：有界 runtime error 环（map 'error' 事件 → dedup → 随观测上报）。
  const runtimeErrorRingRef = useRef(new RuntimeErrorRing())
  // P9：error 监听生命周期（首次 issue 时在真实 map 实例上注册，unmount 清理）。
  const errorHandlerRef = useRef<((e: unknown) => void) | null>(null)
  const errorMapRef = useRef<{ off: (ev: string, h: (e: unknown) => void) => void } | null>(null)

  // Cancel any in-flight observation on unmount so a late response can never
  // setState / dispatch a map action after the panel is gone (INV-6).
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      observationAbortRef.current?.abort()
      observationAbortRef.current = null
      // P9：卸载时摘除 error 监听（listener 生命周期 mount→register→cleanup）。
      const map = errorMapRef.current
      const handler = errorHandlerRef.current
      if (map && handler) map.off('error', handler)
      errorMapRef.current = null
      errorHandlerRef.current = null
    }
  }, [])

  // FE-P2-3：修复总预算按会话重置（新会话 = 新的修复额度）；error 环随会话
  // 清空 —— 旧会话的 runtime error 不得污染新会话的观测。
  useEffect(() => {
    totalRepairsRef.current = 0
    repairBudgetExhaustedWarnedRef.current = false
    runtimeErrorRingRef.current.drain()
  }, [sessionId])

  const issueCartographicObservation = useCallback(({
    map,
    spec,
    layers,
  }: {
    map: any
    spec: MapSpec
    layers: Layer[]
  }) => {
    const generation = layers.reduce<Layer | null>((latest, layer) => {
      if (!layer._mapspecFingerprint) return latest
      if (!latest) return layer
      return (layer._mapspecGenerationAt ?? 0) > (latest._mapspecGenerationAt ?? 0)
        ? layer
        : latest
    }, null)
    if (!map || !sessionId || !generation?._mapspecFingerprint) return
    // 闭包内使用：narrowing 不跨 async 边界 —— 提前固化为 string。
    const fingerprint: string = generation._mapspecFingerprint

    // P9：error 监听一次性注册（幂等；卸载/替换时摘除旧监听）。
    if (getMap && !errorHandlerRef.current) {
      const mapInstance = getMap()
      if (mapInstance) {
        const handler = (e: unknown) => runtimeErrorRingRef.current.push(e)
        try {
          mapInstance.on('error', handler)
          errorHandlerRef.current = handler
          errorMapRef.current = mapInstance
        } catch {
          // 监听失败不阻断观测（错误环为空即可）
        }
      }
    }

    // P9：签发改异步 —— bounded settle 后再采集（MapLibre idle 或超时，
    // 先到为准）。settle 期间的 unmount/会话切换由下方守卫吸收；渲染观察
    // 永不阻塞渲染本身（settle 是 race，无长等待）。
    void (async () => {
      const mapIdle = await waitForRenderSettle(map)
      if (!mountedRef.current || cartographicSessionIdRef.current !== sessionId) return

      const observation = collectRenderObservation({
        map,
        spec,
        layers,
        mapspecFingerprint: fingerprint,
        mapspecRevision: getMapSpecSessionCursor().revision,
        errorRing: runtimeErrorRingRef.current,
        mapIdle,
        reconcileError: runtimeRef.current?.getLastError() ?? '',
        applied: runtimeRef.current?.getAppliedSpec() ?? null,
      })
      // #692：去重键不 stringify 整个 observation——raster_image 是多 MB
      // data URL（docstring 自称 bounded metadata only 被该字段违反），
      // 每次 reconcile 在主线程序列化 MB 级 base64 仅为算键。用稳定字段
      // + raster 载荷的长度引用计数替代（内容变化必然改变长度或指纹）。
      const rasterImg = (observation as { raster_image?: string }).raster_image
      // len + 首尾采样：同长异容（同 bbox 换调色板重渲染）也能变键
      const rasterMark =
        rasterImg === undefined
          ? undefined
          : `len:${rasterImg.length}:${rasterImg.slice(0, 24)}:${rasterImg.slice(-24)}`
      // P9：observed_at 是采集墙钟（每次都变），不参与去重键 —— 否则同
      // 一渲染状态的重复采集永远不命中去重、每个 reconcile 都 POST。
      const { observed_at: _volatileAt, ...stateFields } = observation
      void _volatileAt
      const keyPayload = {
        ...stateFields,
        ...(rasterMark !== undefined ? { raster_image: rasterMark } : {}),
      }
      const observationKey = `${sessionId}:${JSON.stringify(keyPayload)}`
      if (observationKey === lastCartographicObservationKeyRef.current) return
      lastCartographicObservationKeyRef.current = observationKey
      const clientGeneration = Math.max(
        cartographicObservationGenerationRef.current + 1,
        Date.now() * 1000,
      )
      cartographicObservationGenerationRef.current = clientGeneration
      // Capture THIS request's correlation in the closure so a late response
      // compares against the newest issued values, not its own stale view.
      const requestGeneration = clientGeneration
      latestIssuedCartographicFingerprintRef.current = fingerprint
      // A newer observation supersedes any still in flight: abort it so its
      // late response cannot dispatch a stale repair. The generation gate in
      // the handler below is the authoritative guard; the abort just reclaims
      // the round-trip and makes unmount deterministic.
      observationAbortRef.current?.abort()
      const controller = new AbortController()
      observationAbortRef.current = controller
      void apiFetch<{
        repair_action?: import('@/lib/types').MapActionPayload;
        runtime_repair?: {
          applied?: string[];
          exhausted?: boolean;
          passes?: number;
          mapspec?: unknown;
          mutation_revision?: number;
        };
      }>(
        `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/cartographic-observation`,
        {
          method: 'POST',
          body: { ...observation, client_generation: clientGeneration },
          ownerToken,
          signal: controller.signal,
          // Fire-and-forget: evaluation can exceed 30s under a Pi turn's
          // session lock. Superseded by the next observation's abort.
          timeoutMs: 0,
          label: 'Cartographic observation error',
        },
      ).then((response) => {
        // Generation-safe handling — latest generation/fingerprint wins.
        if (!mountedRef.current) return // unmounted: no side effects (INV-6)
        if (cartographicSessionIdRef.current !== sessionId) return // session switch (INV-2)
        if (cartographicObservationGenerationRef.current !== requestGeneration) {
          return // a newer observation was issued → this response is stale (INV-1/INV-7)
        }
        const repair = response.repair_action
        if (repair) {
          const repairParams = repair.params as
            | { mapspec_fingerprint?: string }
            | undefined
          // A repair targeting an older mapspec fingerprint must not mutate a
          // map that has since advanced (INV-4). Only enforced when the backend
          // echoes a fingerprint, so a future field change can't block a valid
          // repair (INV-7).
          if (
            repairParams?.mapspec_fingerprint
            && latestIssuedCartographicFingerprintRef.current !== repairParams.mapspec_fingerprint
          ) return
          // Duplicate response / retry must not re-apply the same repair (INV-3).
          const repairId = repair.action_id
          if (repairId && appliedRepairIdsRef.current.has(repairId)) return
          if (totalRepairsRef.current >= MAX_TOTAL_SESSION_REPAIRS) {
            if (!repairBudgetExhaustedWarnedRef.current) {
              repairBudgetExhaustedWarnedRef.current = true
              devOnly.warn(
                '[map] cartographic repair budget exhausted; repairs suspended for this session',
              )
            }
            return
          }
          totalRepairsRef.current += 1
          dispatchAction(repair)
          // Record AFTER dispatch so a dispatch throw leaves the repair re-issuable.
          if (repairId) {
            const seen = appliedRepairIdsRef.current
            seen.add(repairId)
            if (seen.size > MAX_APPLIED_REPAIR_IDS) {
              seen.delete(seen.keys().next().value as string)
            }
          }
        }
        // ADR-0088 runtime repair：reassert 推进了 revision —— 提交修复后的
        // spec 触发 reconcile 重跑（重新挂载缺失层/组件），settle 后自动再
        // 观察 → 修复回路闭合。commitMapSpecDocument 的旧代次保护拒掉迟到
        // 信道上的旧 spec（同代重提交是幂等 emit）。
        const runtimeRepair = response.runtime_repair
        if (runtimeRepair?.mapspec) {
          commitMapSpecDocument(
            runtimeRepair.mapspec,
            typeof runtimeRepair.mutation_revision === 'number'
              ? runtimeRepair.mutation_revision
              : undefined,
          )
        }
      }).catch((error) => {
        // A supersede/unmount abort is expected: the newer request (or the
        // unmount) owns the state, so stay quiet and leave the key alone.
        if (
          !mountedRef.current
          || error?.name === "AbortError"
          || error instanceof ApiTimeoutError
        ) return
        // Only the LATEST request may reset the observation key for retry — a
        // superseded request failing must not force a redundant re-POST of the
        // newer observation (INV-5: retry without a storm).
        if (cartographicObservationGenerationRef.current !== requestGeneration) return
        lastCartographicObservationKeyRef.current = ''
        devOnly.warn('[map] cartographic observation failed:', error)
      })
    })()
  }, [sessionId, ownerToken, dispatchAction, runtimeRef, getMap])

  return issueCartographicObservation
}
