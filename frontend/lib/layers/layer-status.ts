/**
 * Layer status derivation — closed vocabulary for the Layer Manager V2
 * (Workspace V2 / Goal C2).
 *
 *     loading | ready | rendering | hidden | stale | failed | expired
 *
 * Derivation is READ-ONLY and pure: it projects three existing facts and
 * never writes a parallel status field into the store or MapSpec —
 *
 *   - the HUD store row (`Layer`: visible / _refId / _descriptor / source);
 *   - the committed MapSpec revision (session-cursor, single map truth);
 *   - the latest RenderObservation evidence (`lib/layers/render-evidence`).
 *
 * Semantics (distinct on purpose — see the goal's visibility architecture):
 *
 *   loading    ref-backed data has not landed yet (fetch in flight);
 *   ready      mounted+converged (observed); observation-absent layers
 *              default to ready —— 无反面证据不虚构异常（诚实缺省）；
 *   rendering  desired state changed after the last observation settle
 *              (spec revision advanced past the observed one);
 *   hidden     desired visibility is off (user-owned or agent desired state);
 *   stale      desired state present but runtime diverged / attestation
 *              cleared by a local presentation edit (runtime-repair domain);
 *   failed     bounded runtime error targeting this layer family;
 *   expired    the backing ref was definitively evicted (fetch failed).
 */
import type { Layer } from '@/lib/types/layer';
import { getRefSourceState } from '@/lib/mapspec/ref-source-resolver';
import type { LayerRenderEvidence } from './render-evidence';

export type LayerStatus =
  | 'loading'
  | 'ready'
  | 'rendering'
  | 'hidden'
  | 'stale'
  | 'failed'
  | 'expired';

export const LAYER_STATUS_LABELS: Record<LayerStatus, string> = {
  loading: '加载中',
  ready: '就绪',
  rendering: '渲染中',
  hidden: '已隐藏',
  stale: '待同步',
  failed: '失败',
  expired: '已过期',
};

export interface LayerStatusInput {
  layer: Layer;
  /** Latest render evidence for this layer's HUD id (may be null). */
  evidence?: LayerRenderEvidence | null;
  /** Current committed MapSpec revision (session-cursor). */
  currentRevision?: number;
}

function isRefPending(layer: Layer): boolean {
  // Ref-backed row whose payload has not landed: the SSE mount path creates
  // an empty placeholder FeatureCollection carrying metadata.ref_id, plus
  // `_refId`/`_tileUrl`. MVT-only rows stay "loaded" (tiles stream lazily).
  if (!layer._refId) return false;
  // 服务端在 ref 铸造时就算好的 descriptor 计数：0 = 查询合法空结果
  // （不是「未落地」）—— 空结果显示就绪，而不是永远加载中。
  const d = layer._descriptor;
  if (d && d.feature_count === 0) return false;
  const src = layer.source;
  const hasFeatures = !!src
    && typeof src === 'object'
    && 'features' in src
    && Array.isArray(src.features)
    && src.features.length > 0;
  return !hasFeatures && !layer._tileUrl;
}

/**
 * Derive the layer's status. Pure — same inputs always yield the same status.
 * Data-level failures outrank presentation, presentation outranks transients,
 * steady states last.
 */
export function deriveLayerStatus({
  layer,
  evidence,
  currentRevision = 0,
}: LayerStatusInput): LayerStatus {
  // 1) Data gone: the ref fetch definitively failed (descriptor evicted /
  //    expired server-side). Mirrors the backend artifact_expired semantics.
  if (layer._refId && getRefSourceState(layer._refId) === 'failed') {
    return 'expired';
  }
  // 2) Render-level failure: a bounded runtime error targeted this family.
  if (evidence?.error) {
    return 'failed';
  }
  // 3) Data not yet arrived (ref fetch in flight).
  if (isRefPending(layer)) {
    return 'loading';
  }
  // 4) Desired visibility off (user-owned hide or agent desired state —
  //    the distinction lives in mutation provenance, not here).
  if (!layer.visible) {
    return 'hidden';
  }
  if (evidence) {
    // 5) Desired state changed after the observed generation: awaiting the
    //    next reconcile settle + observation.
    if (currentRevision > evidence.revision) {
      return 'rendering';
    }
    // 6) Desired present but runtime diverged (unmounted or divergent
    //    style/source) — the runtime-repair domain, never recomputation.
    if (!evidence.mounted || !evidence.converged || !evidence.visible) {
      return 'stale';
    }
  }
  // 7) Steady state.
  return 'ready';
}
