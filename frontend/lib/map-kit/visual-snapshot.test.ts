import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  VISUAL_SNAPSHOT_MAX_BYTES,
  VISUAL_SNAPSHOT_MAX_PER_SESSION,
  shouldCaptureVisualSnapshot,
  type VisualSnapshotGateState,
} from './visual-snapshot'
import { ApiError } from '@/lib/api/transport'
import type * as VisualSnapshotModule from './visual-snapshot'

/**
 * captureAndUploadVisualSnapshot 需要动态导入：测试用 vi.doMock 替换
 * ./exporter 的 captureMapCanvas —— 静态导入会把 mock 关在模块缓存外。
 */
async function loadCapture(): Promise<
  typeof VisualSnapshotModule.captureAndUploadVisualSnapshot
> {
  vi.resetModules()
  const { captureAndUploadVisualSnapshot } = await import('./visual-snapshot')
  return captureAndUploadVisualSnapshot
}

const state = (over: Partial<VisualSnapshotGateState> = {}): VisualSnapshotGateState => ({
  lastUploadedRevision: 0,
  lastUploadedAt: 0,
  uploadedCount: 0,
  ...over,
})

describe('shouldCaptureVisualSnapshot', () => {
  it('skips without a usable revision', () => {
    expect(shouldCaptureVisualSnapshot({ revision: 0, now: 1000, state: null }).reason)
      .toBe('no_revision')
    expect(shouldCaptureVisualSnapshot({ revision: -1, now: 1000, state: null }).capture)
      .toBe(false)
  })

  it('allows the first capture of a session', () => {
    const d = shouldCaptureVisualSnapshot({ revision: 4, now: 1000, state: null })
    expect(d).toEqual({ capture: true, reason: 'allowed' })
  })

  it('skips when revision is unchanged', () => {
    const d = shouldCaptureVisualSnapshot({
      revision: 4,
      now: 100_000,
      state: state({ lastUploadedRevision: 4, lastUploadedAt: 1000 }),
    })
    expect(d).toEqual({ capture: false, reason: 'revision_unchanged' })
  })

  it('enforces the minimum interval', () => {
    const d = shouldCaptureVisualSnapshot({
      revision: 5,
      now: 1000 + 29_999,
      state: state({ lastUploadedRevision: 4, lastUploadedAt: 1000 }),
    })
    expect(d).toEqual({ capture: false, reason: 'rate_limited' })
    const ok = shouldCaptureVisualSnapshot({
      revision: 5,
      now: 1000 + 30_000,
      state: state({ lastUploadedRevision: 4, lastUploadedAt: 1000 }),
    })
    expect(ok.capture).toBe(true)
  })

  it('enforces the per-session budget (backend FIFO width)', () => {
    const d = shouldCaptureVisualSnapshot({
      revision: 9,
      now: 100_000,
      state: state({
        lastUploadedRevision: 8,
        lastUploadedAt: 1000,
        uploadedCount: VISUAL_SNAPSHOT_MAX_PER_SESSION,
      }),
    })
    expect(d).toEqual({ capture: false, reason: 'budget_exhausted' })
  })
})

describe('captureAndUploadVisualSnapshot', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn(async () => new Response(
      JSON.stringify({ ref: 'vshot-abc', sha256: 'f'.repeat(64), size: 10 }),
      { status: 200 },
    ))
    vi.stubGlobal('fetch', fetchMock)
  })

  afterEach(() => {
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it('uploads after passing the gate and updates the gate state', async () => {
    const blob = new Blob([new Uint8Array([0x89, 0x50])], { type: 'image/png' })
    vi.doMock('./exporter', async (importOriginal) => {
      const mod = await importOriginal<typeof import('./exporter')>()
      return { ...mod, captureMapCanvas: vi.fn(async () => blob) }
    })
    const freshCapture = await loadCapture()
    const stateRef = { current: null as VisualSnapshotGateState | null }
    const result = await freshCapture({
      map: { getCanvas: () => ({}) },
      sessionId: 's1',
      revision: 7,
      fingerprint: 'carto-sha256:abc',
      stateRef,
    })
    expect(result.uploaded).toBe(true)
    expect(result.ref).toBe('vshot-abc')
    expect(stateRef.current?.lastUploadedRevision).toBe(7)
    expect(stateRef.current?.uploadedCount).toBe(1)
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit]
    expect(url).toContain('/visual-snapshots')
    expect(url).toContain('mapspec_revision=7')
    expect(url).toContain('mapspec_fingerprint=carto-sha256%3Aabc')
    expect(init.method).toBe('POST')
    expect(init.body).toBeInstanceOf(FormData)
  })

  it('does not capture when the gate skips', async () => {
    const stateRef = { current: state({ lastUploadedRevision: 3, lastUploadedAt: Date.now() }) }
    const captureAndUploadVisualSnapshot = await loadCapture()
    const result = await captureAndUploadVisualSnapshot({
      map: {}, sessionId: 's1', revision: 3, fingerprint: 'fp', stateRef,
    })
    expect(result).toEqual({ uploaded: false, reason: 'revision_unchanged' })
    expect(fetchMock).not.toHaveBeenCalled()
  })

  it('fails open on transport errors (no throw)', async () => {
    fetchMock.mockRejectedValue(new ApiError(503, 'busy'))
    vi.doMock('./exporter', async (importOriginal) => {
      const mod = await importOriginal<typeof import('./exporter')>()
      return { ...mod, captureMapCanvas: vi.fn(async () => new Blob([new Uint8Array([1])])) }
    })
    const freshCapture = await loadCapture()
    const stateRef = { current: null as VisualSnapshotGateState | null }
    const result = await freshCapture({
      map: {}, sessionId: 's1', revision: 7, fingerprint: 'fp', stateRef,
    })
    expect(result.uploaded).toBe(false)
    expect(result.reason).toBe('capture_or_upload_failed')
    expect(stateRef.current).toBeNull()
  })

  it('pre-checks the backend size limit client-side', async () => {
    const big = new Blob([new Uint8Array(VISUAL_SNAPSHOT_MAX_BYTES + 1)])
    vi.doMock('./exporter', async (importOriginal) => {
      const mod = await importOriginal<typeof import('./exporter')>()
      return { ...mod, captureMapCanvas: vi.fn(async () => big) }
    })
    const freshCapture = await loadCapture()
    const stateRef = { current: null as VisualSnapshotGateState | null }
    const result = await freshCapture({
      map: {}, sessionId: 's1', revision: 7, fingerprint: 'fp', stateRef,
    })
    expect(result).toEqual({ uploaded: false, reason: 'oversized_capture' })
    expect(fetchMock).not.toHaveBeenCalled()
  })
})
