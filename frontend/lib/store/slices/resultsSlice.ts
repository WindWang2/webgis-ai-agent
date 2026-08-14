/**
 * Results slice — GIS Analysis Result Workbench registry.
 *
 * Session-scoped, bounded, NON-persisted (deliberately excluded from the
 * `geoagent-settings` partialize, exactly like `layers`: results reference
 * session-only `ref:` cursors that would be dead after reload).
 *
 * Fed by the SSE stream: `captureToolCallArgs` stashes the preceding tool_call
 * args (best-effort, keyed by tool name within the turn), and `captureStepResult`
 * normalizes a `step_result` event into the shared model. The registry only
 * mutates on `step_result`/`tool_call` — never on token events — so token
 * streaming cannot rerender the workbench (spec §16).
 */
import type { StateCreator } from 'zustand';
import type { HudState } from '../hud-types';
import { normalizeStepResult, parseBBox } from '@/lib/results/normalize';
import type { AnalysisResult, LayerDescriptor, StepResultEvent } from '@/lib/results/types';

/** Bounded history (spec §16: bounded history, no unbounded growth). */
export const MAX_RESULTS = 50;

function normalizeGeometryTypes(g: LayerDescriptor['geometry_types']): string[] | undefined {
  if (!g) return undefined;
  if (Array.isArray(g)) return g.length ? g.map(String) : undefined;
  if (typeof g === 'object' && g) {
    const keys = Object.keys(g);
    return keys.length ? keys : undefined;
  }
  return undefined;
}

function parseArgs(raw: string): Record<string, any> | undefined {
  if (!raw) return undefined;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : undefined;
  } catch {
    return undefined;
  }
}

/** Pure-orchestration / map-action tools that carry no inspectable analysis result. */
const IGNORED_TOOLS = new Set(['propose_plan', 'display_layer']);

/**
 * Strip heavy payload keys from the stored `raw` so the bounded registry (up to
 * MAX_RESULTS entries) cannot hold dozens of multi-MB FeatureCollections in
 * memory. The slim metadata (summary / stats / legend_spec / echoed scalars) is
 * retained for the advanced "raw result" disclosure; the feature/image payload
 * is already loaded on the map and is not actionable in raw JSON.
 */
const HEAVY_RAW_KEYS = new Set(['data', 'image', 'geojson', 'features', 'grid', 'data_list']);
function sanitizeRaw(raw: unknown): unknown {
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(raw as Record<string, unknown>)) {
      if (!HEAVY_RAW_KEYS.has(k)) out[k] = v;
    }
    return out;
  }
  return raw;
}

export const createResultsSlice: StateCreator<HudState, [], [], Partial<HudState>> = (set) => {
  // F-3: pending tool_call args, keyed by tool name as a FIFO queue. The
  // previous last-writer-per-tool slot meant two same-tool calls in one turn
  // collided: the first step_result was attached the SECOND call's args, and
  // the second got none. A queue pairs each step_result with its own preceding
  // tool_call args in order.
  let pendingArgs: Record<string, Record<string, any>[]> = {};

  return {
    /* ─── Analysis Results Workbench ─── */
    results: [],
    selectedResultId: null,

    captureToolCallArgs: (tool, argsStr) => {
      const parsed = parseArgs(argsStr);
      if (!parsed) return;
      const queue = pendingArgs[tool] ? [...pendingArgs[tool], parsed] : [parsed];
      pendingArgs = { ...pendingArgs, [tool]: queue };
    },

    discardPendingToolArgs: (tool) => {
      // R2F-2: a failed/cancelled call never emits its step_result, so its
      // queued args would otherwise be FIFO-consumed by the RETRY's
      // step_result (mispairing the workbench input evidence with the wrong
      // attempt). Drop the oldest queued entry for this tool.
      const queue = pendingArgs[tool];
      if (!queue || !queue.length) return;
      const nextQueue = queue.slice(1);
      const rest = { ...pendingArgs };
      if (nextQueue.length) rest[tool] = nextQueue;
      else delete rest[tool];
      pendingArgs = rest;
    },

    captureStepResult: (stepInput: StepResultEvent) => {
      // Ignore pure orchestration/map-action events that carry no inspectable result.
      const step = stepInput as StepResultEvent;
      if (!step || !step.tool || IGNORED_TOOLS.has(step.tool)) return undefined;

      // FIFO-consume the oldest captured args for this tool.
      const queue = pendingArgs[step.tool];
      const args = queue && queue.length ? queue[0] : undefined;
      const base = normalizeStepResult(step, { captured: !!args, args });
      const normalized: AnalysisResult = {
        ...base,
        capturedAt: Date.now(),
        raw: sanitizeRaw(base.raw),
      };
      // Shift the consumed entry; drop the tool key once its queue is empty so a
      // later same-name call does not reuse stale args.
      if (queue && queue.length) {
        const nextQueue = queue.slice(1);
        const rest = { ...pendingArgs };
        if (nextQueue.length) rest[step.tool] = nextQueue;
        else delete rest[step.tool];
        pendingArgs = rest;
      }

      set((s) => {
        // Dedup by id (re-emitted step_result for the same step updates in place).
        const without = s.results.filter((r) => r.id !== normalized.id);
        const next = [normalized, ...without].slice(0, MAX_RESULTS);
        return { results: next };
      });
      return normalized.id;
    },

    enrichResultOutput: (resultId, ref, descriptor) =>
      set((s) => ({
        results: s.results.map((r) => {
          if (r.id !== resultId) return r;
          const outputs = r.outputs.map((o) => {
            if (o.ref !== ref) return o;
            return {
              ...o,
              featureCount: descriptor.feature_count ?? o.featureCount,
              geometryTypes: normalizeGeometryTypes(descriptor.geometry_types) ?? o.geometryTypes,
              // Prefer the event's bbox (computed server-side from full data); only
              // fall back to the descriptor's (≤5000-feature sample) bbox when absent.
              bbox: o.bbox ?? parseBBox(descriptor.bbox),
              estimatedBytes: descriptor.estimated_bytes ?? o.estimatedBytes,
              // CRS intentionally NOT derived from the descriptor (it carries none);
              // it stays `undefined` ⇒ UI renders "Unknown" (spec §9, never fabricated).
            };
          });
          const bbox = r.bbox ?? parseBBox(descriptor.bbox);
          return { ...r, outputs, bbox };
        }),
      })),

    selectResult: (id) => set({ selectedResultId: id }),

    removeResult: (id) =>
      set((s) => ({
        results: s.results.filter((r) => r.id !== id),
        selectedResultId: s.selectedResultId === id ? null : s.selectedResultId,
      })),

    clearResults: () => {
      pendingArgs = {};
      set({ results: [], selectedResultId: null });
    },
  };
};
