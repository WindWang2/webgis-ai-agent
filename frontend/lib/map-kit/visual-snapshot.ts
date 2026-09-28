/**
 * C13 — 受控视觉截图采集与上传（stale 硬门的客户端半边）。
 *
 * 设计边界：
 * - **受控**：只有「观察被服务端接受」这一时机才允许采集（渲染已 settle、
 *   (revision, fingerprint) 由服务端盖章权威回传）；revision 未变化不重拍；
 *   最小间隔 + 每会话上限（与后端 FIFO 同宽 8）—— 三重门防截图洪泛。
 * - **fail-open**：采集/上传的任何失败只记 debug —— 截图是视觉回路的
 *   增值证据，绝不影响渲染、观察或修复主链（后端诚实缺席 = no_screenshot）。
 * - **ref-only**：上传走既有 `/visual-snapshots` 通道（PNG 魔数 + 4MiB 门），
 *   前端不持有、不回显字节。
 */

import { apiFetch } from '@/lib/api/transport'
import { captureMapCanvas } from '@/lib/map-kit/exporter'
import { devOnly } from '@/lib/utils/logger'

/** 最小采集间隔（ms）：同一会话两次上传的墙钟下限。 */
export const VISUAL_SNAPSHOT_MIN_INTERVAL_MS = 30_000
/** 每会话上传上限 —— 与后端截图索引 FIFO（MAX_SCREENSHOTS_PER_SESSION）同宽。 */
export const VISUAL_SNAPSHOT_MAX_PER_SESSION = 8
/** 后端单图上限（4MiB）的客户端预检 —— 避免注定被拒的上传。 */
export const VISUAL_SNAPSHOT_MAX_BYTES = 4 * 1024 * 1024

/** 会话级采集门状态（hook 持有，随会话切换重置）。 */
export interface VisualSnapshotGateState {
  lastUploadedRevision: number
  lastUploadedAt: number
  uploadedCount: number
}

export type VisualSnapshotSkipReason =
  | 'no_revision'
  | 'revision_unchanged'
  | 'rate_limited'
  | 'budget_exhausted'

export interface VisualSnapshotDecision {
  capture: boolean
  reason: VisualSnapshotSkipReason | 'allowed'
}

/**
 * 纯采集门（机器可测）：revision 变化 + 最小间隔 + 会话预算。
 * `state === null` 视为会话首拍。
 */
export function shouldCaptureVisualSnapshot(input: {
  revision: number
  now: number
  state: VisualSnapshotGateState | null
  minIntervalMs?: number
  maxPerSession?: number
}): VisualSnapshotDecision {
  const {
    revision,
    now,
    state,
    minIntervalMs = VISUAL_SNAPSHOT_MIN_INTERVAL_MS,
    maxPerSession = VISUAL_SNAPSHOT_MAX_PER_SESSION,
  } = input
  if (!Number.isFinite(revision) || revision <= 0) {
    return { capture: false, reason: 'no_revision' }
  }
  if (state && state.lastUploadedRevision === revision) {
    return { capture: false, reason: 'revision_unchanged' }
  }
  if (state && now - state.lastUploadedAt < minIntervalMs) {
    return { capture: false, reason: 'rate_limited' }
  }
  if (state && state.uploadedCount >= maxPerSession) {
    return { capture: false, reason: 'budget_exhausted' }
  }
  return { capture: true, reason: 'allowed' }
}

export interface VisualSnapshotUploadResult {
  uploaded: boolean
  ref?: string
  sha256?: string
  reason?: string
}

/**
 * 采集当前画布并上传到 `/visual-snapshots`（观察被接受后调用 ——
 * revision 是服务端盖章值，fingerprint 是本次观察通过的指纹）。
 * 任何失败 fail-open：返回 `{ uploaded: false, reason }`，绝不抛出。
 */
export async function captureAndUploadVisualSnapshot(opts: {
  map: unknown
  sessionId: string
  ownerToken?: string | null
  revision: number
  fingerprint: string
  stateRef: { current: VisualSnapshotGateState | null }
  now?: number
}): Promise<VisualSnapshotUploadResult> {
  const { map, sessionId, ownerToken, revision, fingerprint, stateRef } = opts
  const decision = shouldCaptureVisualSnapshot({
    revision,
    now: opts.now ?? Date.now(),
    state: stateRef.current,
  })
  if (!decision.capture) {
    return { uploaded: false, reason: decision.reason }
  }
  try {
    const blob = await captureMapCanvas(map as Parameters<typeof captureMapCanvas>[0])
    if (!blob || blob.size === 0) {
      return { uploaded: false, reason: 'empty_capture' }
    }
    if (blob.size > VISUAL_SNAPSHOT_MAX_BYTES) {
      return { uploaded: false, reason: 'oversized_capture' }
    }
    const form = new FormData()
    form.append('screenshot', blob, 'map.png')
    const params = new URLSearchParams({
      mapspec_revision: String(revision),
      mapspec_fingerprint: fingerprint,
    })
    const response = await apiFetch<{
      ref?: string
      sha256?: string
    }>(
      `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/visual-snapshots?${params.toString()}`,
      {
        method: 'POST',
        rawBody: form,
        ownerToken,
        timeoutMs: 0,
        label: 'Visual snapshot upload error',
      },
    )
    if (!response?.ref) {
      return { uploaded: false, reason: 'upload_unconfirmed' }
    }
    const now = opts.now ?? Date.now()
    stateRef.current = {
      lastUploadedRevision: revision,
      lastUploadedAt: now,
      uploadedCount: (stateRef.current?.uploadedCount ?? 0) + 1,
    }
    devOnly.log('[visual-snapshot] uploaded', revision, response.ref.slice(0, 24))
    return { uploaded: true, ref: response.ref, sha256: response.sha256 }
  } catch (error) {
    devOnly.log('[visual-snapshot] capture/upload failed', error)
    return { uploaded: false, reason: 'capture_or_upload_failed' }
  }
}
