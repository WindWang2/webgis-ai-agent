/**
 * Layer render evidence — bounded client-side stash of the LATEST
 * RenderObservation's per-layer verdicts (Workspace V2 / Goal C2).
 *
 * This is a *projection of an observation*, not new map truth: MapSpec stays
 * the only desired-state authority (ADR-0088). The layer manager derives a
 * closed status vocabulary from three existing facts — the store row, the
 * committed MapSpec revision, and this observed-runtime evidence — instead of
 * keeping a parallel status field in the store.
 *
 * Bounded by contract: latest observation only, ≤ MAX_TRACKED_LAYERS ids,
 * booleans + revision + timestamp — never GeoJSON or feature payloads.
 */

export interface LayerRenderEvidence {
  /** Runtime layer family mounted (runtime_layer_count > 0). */
  mounted: boolean;
  /** Observed visibility (all live sublayers visible). */
  visible: boolean;
  /** Style + source converged at observation time. */
  converged: boolean;
  /** Bounded runtime error message targeting this layer (if any). */
  error?: string;
  /** Session-cursor revision the observation was collected against. */
  revision: number;
  /** Collection wall-clock (ms) — diagnostics only, never a guard. */
  at: number;
}

/** Cap on tracked layer ids (bounded memory; latest-wins per id). */
export const MAX_TRACKED_LAYERS = 128;

const evidenceByHudId = new Map<string, LayerRenderEvidence>();
const listeners = new Set<() => void>();
let generation = 0;

function emit(): void {
  generation += 1;
  listeners.forEach((listener) => listener());
}

export function subscribeLayerEvidence(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getLayerEvidenceGeneration(): number {
  return generation;
}

/** Latest evidence for a HUD layer id (`runtime_store_id` of the observation). */
export function getLayerEvidence(hudLayerId: string): LayerRenderEvidence | null {
  return evidenceByHudId.get(hudLayerId) ?? null;
}

/**
 * Record the latest observation's per-layer verdicts (best-effort, pure
 * projection — called from the observation hook after each settle).
 *
 * Accepts the raw observation shape (`layers[]` entries carry
 * `runtime_store_id` / `runtime_layer_count` / `visible` /
 * `source_converged` / `style_converged`) plus the bounded runtime errors so
 * per-layer failures can surface as `failed` status.
 */
export function recordLayerEvidence(observation: {
  layers?: Array<Record<string, unknown>>;
  runtime_errors?: Array<{ message?: string; target?: string }>;
  reconcile_error?: string;
}, currentRevision: number): void {
  const layers = Array.isArray(observation.layers) ? observation.layers : [];
  // 空观察（合法数组、零层）= 全部层已离开 runtime —— 清空旧证据，
  // 不让缺席层的 stale/failed 判定残留。
  if (!layers.length) {
    if (evidenceByHudId.size) {
      evidenceByHudId.clear();
      emit();
    }
    return;
  }
  const at = Date.now();
  const next = new Map<string, LayerRenderEvidence>();
  // spec 层 id → HUD 行 id（错误 target 是 MapLibre 层/源 id —— agent 授权
  // 层的 spec id 与 HUD id 可能不同，双索引才能把错误归到行）。
  const specIdToHud = new Map<string, string>();
  for (const entry of layers) {
    if (!entry || typeof entry !== 'object') continue;
    const hudId = typeof entry.runtime_store_id === 'string'
      ? entry.runtime_store_id
      : (typeof entry.id === 'string' ? entry.id : '');
    if (!hudId) continue;
    if (typeof entry.id === 'string' && entry.id !== hudId) {
      specIdToHud.set(entry.id.split('__')[0], hudId);
    }
    const count = typeof entry.runtime_layer_count === 'number'
      ? entry.runtime_layer_count
      : 0;
    next.set(hudId, {
      mounted: count > 0,
      visible: entry.visible !== false,
      converged: entry.source_converged !== false && entry.style_converged !== false,
      revision: currentRevision,
      at,
    });
  }
  for (const err of observation.runtime_errors ?? []) {
    if (!err || typeof err.target !== 'string' || !err.target) continue;
    // Errors may target a spec sublayer id (`layer__sub`) or the family id.
    const family = err.target.split('__')[0];
    const hit = next.get(family) ?? next.get(specIdToHud.get(family) ?? '');
    if (hit) hit.error = String(err.message ?? '').slice(0, 160);
  }
  if (observation.reconcile_error) {
    for (const value of next.values()) value.error = String(observation.reconcile_error).slice(0, 160);
  }
  // Bounded: keep the first MAX_TRACKED_LAYERS entries in observation
  // order (deterministic; observations are latest-wins per id already).
  const entries = [...next.entries()].slice(0, MAX_TRACKED_LAYERS);
  evidenceByHudId.clear();
  for (const [id, value] of entries) evidenceByHudId.set(id, value);
  emit();
}

/** Session switch / teardown: evidence belongs to one session's runtime. */
export function clearLayerEvidence(): void {
  if (!evidenceByHudId.size) return;
  evidenceByHudId.clear();
  emit();
}
