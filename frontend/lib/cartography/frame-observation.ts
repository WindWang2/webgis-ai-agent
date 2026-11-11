/**
 * G25 逐画幅真实观察生产者（P6）。
 *
 * 契约（与后端 frame_products/verification.py 的 OBSERVATION_MODES 对齐）：
 * - 每帧行：{frame_id, mode, mapspec_revision, content_revision?, fingerprint?,
 *   camera?, source_error?, observed_at} —— mode ∈ live_observed / snapshot /
 *   not_observed / stale_observation（export_verified 是后端导出面独有，
 *   浏览器运行时不冒充）；
 * - **不撒谎**：未挂载帧 = not_observed；快照帧 = snapshot + 指纹（显式
 *   非 live）；旧 revision 快照/实例 = stale_observation（不宣称 verified）；
 *   frameA 完成不得补 frameB（每行独立采集）；
 * - 修订对照：mapspec_revision 用**渲染落定**的 resolved（非 desired）；
 * - 页面挂点：window.__g25FrameObservation() → rows（e2e / G28 评估只读
 *   通道）；上报桥接：collectFrameObservationRows() 供既有
 *   cartographic-observation 请求体携带 frames 字段（加性）。
 */
import type { FrameRuntimeSlotView } from '@/lib/cartography/frame-runtime';

export type FrameObservationMode =
  | 'live_observed'
  | 'snapshot'
  | 'not_observed'
  | 'stale_observation';

export interface FrameObservationRow {
  frame_id: string;
  mode: FrameObservationMode;
  /** 渲染落定的 spec revision（resolved；desired 不冒充）。 */
  mapspec_revision: number;
  content_revision: number | null;
  /** 快照指纹（snapshot 模式必带）。 */
  fingerprint: string | null;
  camera: { center: [number, number]; zoom: number } | null;
  source_error: string | null;
  observed_at: number;
}

export interface FrameObservationInput {
  sessionId: string;
  specRevision: number;
  slots: FrameRuntimeSlotView[];
  snapshotStats: { count: number; bytes: number };
  liveCount: number;
  budget: number;
}

/** 每帧独立映射（绝不互借证据）。 */
export function collectFrameObservationRows(input: FrameObservationInput): FrameObservationRow[] {
  return input.slots.map((slot) => {
    let mode: FrameObservationMode;
    if (slot.mountState === 'live') mode = 'live_observed';
    else if (slot.mountState === 'snapshot') mode = 'snapshot';
    else if (slot.mountState === 'stale' || slot.mountState === 'error') mode = 'stale_observation';
    else mode = 'not_observed'; // unmounted / loading（未落定 = 未观察，不猜）
    const resolved = slot.resolvedSpecRevision;
    return {
      frame_id: slot.frameId,
      mode,
      mapspec_revision: resolved !== null && resolved >= 0 ? resolved : input.specRevision,
      content_revision: slot.resolvedDataRevision,
      fingerprint: mode === 'snapshot' ? slot.snapshotFingerprint : null,
      camera: slot.cameraDraft
        ? { center: slot.cameraDraft.center, zoom: slot.cameraDraft.zoom }
        : null,
      source_error: slot.error ? `${slot.error.code}:${slot.error.message}`.slice(0, 160) : null,
      observed_at: Date.now(),
    };
  });
}

declare global {
  interface Window {
    __g25FrameObservation?: () => {
      sessionId: string;
      rows: FrameObservationRow[];
      snapshotStats: { count: number; bytes: number };
      liveCount: number;
      budget: number;
      publishedAt: number;
    };
  }
}

/** 模块级最新行（cartographic-observation POST 的 frames 增维数据源）。 */
let latestRows: FrameObservationRow[] | null = null;

/** 供 collectRenderObservation 并入观察体（无发布过 = null → 缺席语义）。 */
export function latestFrameObservationRows(): FrameObservationRow[] | null {
  return latestRows;
}

/** 页面挂点（grid 每次投影刷新时发布；e2e/评估只读）。 */
export function publishFrameObservations(input: FrameObservationInput): FrameObservationRow[] {
  const rows = collectFrameObservationRows(input);
  latestRows = rows;
  const payload = {
    sessionId: input.sessionId,
    rows,
    snapshotStats: input.snapshotStats,
    liveCount: input.liveCount,
    budget: input.budget,
    publishedAt: Date.now(),
  };
  try {
    if (typeof window !== 'undefined') {
      window.__g25FrameObservation = () => payload;
    }
  } catch { /* 挂点失败不影响主链路 */ }
  return rows;
}
