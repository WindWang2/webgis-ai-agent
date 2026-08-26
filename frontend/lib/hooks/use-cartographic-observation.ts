'use client';
import { useCallback, useEffect, useRef } from 'react';
import type { MapSpecRuntime } from '@/lib/mapspec-runtime';
import { collectCartographicRuntimeObservation } from '@/lib/mapspec-runtime';
import { apiFetch } from '@/lib/api/transport';
import { devOnly } from '@/lib/utils/logger';
import type { Layer } from '@/lib/types/layer';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import type { MapActionPayload } from '@/lib/types';

// Cap on remembered applied repair action_ids (bounded memory; older ids are
// stale generations that the generation gate already drops, so eviction is safe).
const MAX_APPLIED_REPAIR_IDS = 16;

interface UseCartographicObservationOptions {
  /** 共享的 MapSpecRuntime ref（读取 getLastError/getAppliedSpec 作观测证据）。 */
  runtimeRef: React.RefObject<MapSpecRuntime | null>;
  sessionId?: string | null;
  ownerToken?: string | null;
  dispatchAction: (action: MapActionPayload) => void;
}

/**
 * 制图观测→修复回路（单职责：reconcile 落定后采集一次运行时观测并按
 * generation 安全规则派发修复）。包含：clientGeneration 单调门、
 * AbortController 超越取消、修复 action_id 去重环（上限 16）、以及
 * unmounted / 会话切换 / 更新 generation / 过期指纹 / 重复修复五重
 * 响应守卫。返回 issueCartographicObservation —— 由 reconcile 的
 * .then() 以当次闭包的 map/spec/layers 调用（闭包捕获即原始语义：
 * 迟到响应与最新签发值比较后被丢弃）。
 */
export function useCartographicObservation({
  runtimeRef,
  sessionId,
  ownerToken,
  dispatchAction,
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
  const mountedRef = useRef(true)
  const observationAbortRef = useRef<AbortController | null>(null)

  // Cancel any in-flight observation on unmount so a late response can never
  // setState / dispatch a map action after the panel is gone (INV-6).
  useEffect(() => {
    mountedRef.current = true
    return () => {
      mountedRef.current = false
      observationAbortRef.current?.abort()
      observationAbortRef.current = null
    }
  }, [])

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
    const observation = collectCartographicRuntimeObservation(
      map,
      spec,
      layers,
      generation._mapspecFingerprint,
      runtimeRef.current?.getLastError() ?? '',
      runtimeRef.current?.getAppliedSpec() ?? null,
    )
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
    const keyPayload = {
      ...observation,
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
    latestIssuedCartographicFingerprintRef.current = generation._mapspecFingerprint
    // A newer observation supersedes any still in flight: abort it so its
    // late response cannot dispatch a stale repair. The generation gate in
    // the handler below is the authoritative guard; the abort just reclaims
    // the round-trip and makes unmount deterministic.
    observationAbortRef.current?.abort()
    const controller = new AbortController()
    observationAbortRef.current = controller
    void apiFetch<{ repair_action?: import('@/lib/types').MapActionPayload }>(
      `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/cartographic-observation`,
      {
        method: 'POST',
        body: { ...observation, client_generation: clientGeneration },
        ownerToken,
        signal: controller.signal,
        label: 'Cartographic observation error',
      },
    ).then((response) => {
      const repair = response.repair_action
      if (!repair) return
      // Generation-safe dispatch — latest generation/fingerprint wins.
      if (!mountedRef.current) return // unmounted: no side effects (INV-6)
      if (cartographicSessionIdRef.current !== sessionId) return // session switch (INV-2)
      if (cartographicObservationGenerationRef.current !== requestGeneration) {
        return // a newer observation was issued → this response is stale (INV-1/INV-7)
      }
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
      dispatchAction(repair)
      // Record AFTER dispatch so a dispatch throw leaves the repair re-issuable.
      if (repairId) {
        const seen = appliedRepairIdsRef.current
        seen.add(repairId)
        if (seen.size > MAX_APPLIED_REPAIR_IDS) {
          seen.delete(seen.keys().next().value as string)
        }
      }
    }).catch((error) => {
      // A supersede/unmount abort is expected: the newer request (or the
      // unmount) owns the state, so stay quiet and leave the key alone.
      if (!mountedRef.current || error?.name === "AbortError") return
      // Only the LATEST request may reset the observation key for retry — a
      // superseded request failing must not force a redundant re-POST of the
      // newer observation (INV-5: retry without a storm).
      if (cartographicObservationGenerationRef.current !== requestGeneration) return
      lastCartographicObservationKeyRef.current = ''
      devOnly.warn('[map] cartographic observation failed:', error)
    })
  }, [sessionId, ownerToken, dispatchAction, runtimeRef])

  return issueCartographicObservation
}
