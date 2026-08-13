/**
 * Analysis Result Workbench — shared result model (semantic primitives).
 *
 * Design (see .scratch/analysis-workbench/DESIGN.md): one normalized model fed by
 * the SSE `step_result` event, rendered by a small set of reusable section
 * components. Tool families differ only in *metric extraction* (data-driven rules
 * in families.ts), never in their rendering components.
 *
 * Truthfulness contract (spec §9):
 *  - CRS is never fabricated. `crs` is `undefined` ⇒ render "Unknown".
 *  - `featureCount`/`geometryTypes` are only set when backed by evidence
 *    (descriptor endpoint or an echoed scalar). Otherwise omitted ⇒ "not reported".
 *  - Warnings are inferred from real signals (summary prose, `_streaming_note`,
 *    `correction_hint`); they are never invented.
 */
import type { LegendSpec } from '@/lib/map-kit/types';

/** Bounding box as [west, south, east, north]. */
export type BBox = [number, number, number, number];

export type ResultFamily =
  | 'buffer'
  | 'overlay'
  | 'spatial_stats'
  | 'hotspot'
  | 'cluster'
  | 'density'
  | 'interpolation'
  | 'network'
  | 'raster'
  | 'h3'
  | 'remote_sensing'
  | 'cartography'
  | 'generic';

/**
 * Coarse result status derived from the slim_event. `partial`/`warning` surface
 * meaningful caveats without marking the run a failure.
 */
export type ResultStatus =
  | 'completed'
  | 'failed'
  | 'partial'
  | 'warning'
  | 'running'
  | 'unknown';

export type OutputKind = 'vector' | 'raster' | 'statistic' | 'table' | 'image' | 'none';

export interface InputRef {
  /** Session ref (ref:geojson-…) or layer id, when inferable from the tool args. */
  ref?: string;
  /** Human label — the layer name when resolvable, else the arg key. */
  label: string;
  /** Field/column the operation acted on, when stated in the args. */
  field?: string;
  /** True when the input was inferred rather than read from a captured arg. */
  inferred?: boolean;
}

export interface ResultParam {
  label: string;
  value: string | number | boolean;
  /** Original arg key, for transparency in the "raw parameters" view. */
  source?: string;
}

export interface ResultMetric {
  label: string;
  value: string | number;
  emphasis?: 'primary' | 'secondary';
  unit?: string;
}

export type WarningLevel = 'info' | 'warning' | 'error';

export interface ResultWarning {
  level: WarningLevel;
  /** Stable machine code (e.g. `geometry_dropped`, `unreachable_facilities`). */
  code: string;
  message: string;
}

export interface OutputDescriptor {
  kind: OutputKind;
  /** Session ref (ref:geojson-…) when the output backs a fetchable layer. */
  ref?: string;
  /** Geometry type(s) — only when backed by descriptor/echoed evidence. */
  geometryTypes?: string[];
  /** Feature count — only when truthfully known. */
  featureCount?: number;
  /** CRS — only when truthfully known; `undefined` means unknown (never EPSG:4326 fallback). */
  crs?: string;
  /** Bounding box [W,S,E,N] when known. */
  bbox?: BBox;
  /** Whether a map layer is currently bound to this output in the store. */
  hasLayer: boolean;
  /** Estimated bytes (from descriptor) — drives "large output" hints without downloading. */
  estimatedBytes?: number;
  /** Truthful note for non-layer outputs (raster path present, image pushed, …). */
  note?: string;
}

export interface LayerBinding {
  /** Store layer id (equals the ref for tool results). */
  layerId: string;
  visible: boolean;
  name: string;
}

export interface ProvenanceLink {
  label: string;
  detail?: string;
  kind: 'input' | 'operation' | 'output' | 'run';
}

export type SuggestedActionKind =
  | 'show_on_map'
  | 'hide'
  | 'zoom'
  | 'style'
  | 'export'
  | 'overlay'
  | 'buffer'
  | 'classify'
  | 'inspect';

export interface SuggestedAction {
  kind: SuggestedActionKind;
  label: string;
  /** Whether the action is currently actionable (e.g. requires a bound layer). */
  available: boolean;
}

export interface AnalysisResult {
  /** Stable id — `step_id` when present, else a synthetic fallback. */
  id: string;
  tool: string;
  toolLabel: string;
  family: ResultFamily;
  status: ResultStatus;
  /** A durable background job is still running for this result. */
  running?: boolean;
  /** Epoch ms when the result was captured into the registry. */
  capturedAt?: number;
  completedAt?: string;
  durationMs?: number;
  summary?: string;
  inputs: InputRef[];
  parameters: ResultParam[];
  metrics: ResultMetric[];
  warnings: ResultWarning[];
  outputs: OutputDescriptor[];
  bbox?: BBox;
  legendSpec?: LegendSpec;
  layerBindings: LayerBinding[];
  provenance: ProvenanceLink[];
  suggestedActions: SuggestedAction[];
  /** Linked durable job ids (status cross-link via /tasks/jobs). */
  backgroundJobIds: string[];
  /** Progressive-disclosure raw slim_event — never auto-rendered. */
  raw: unknown;
  /** A meaningful approximation/partial signal was detected. */
  approximate?: boolean;
}

/* ─── Inputs to the normalizer ──────────────────────────────────────────────── */

/** Shape of the SSE `step_result` event payload (execution_engine.py:916-932). */
export interface StepResultEvent {
  task_id?: string;
  step_id?: string;
  tool: string;
  result?: Record<string, any> | null;
  geojson_ref?: string | null;
  session_id?: string;
  background_job_ids?: string[] | null;
}

/**
 * Best-effort tool-call arguments captured from the preceding `tool_call` event,
 * used for input evidence + meaningful parameters. Correlation is by tool name
 * within the turn; `captured: false` means args were unavailable and the
 * normalizer must degrade gracefully (truthful "inferred"/"not reported").
 */
export interface ArgsContext {
  args?: Record<string, any>;
  captured: boolean;
}

/** Response of `GET /layers/descriptor/{ref_id}` (layer.py:125-164). */
export interface LayerDescriptor {
  ref_id?: string;
  feature_count?: number;
  point_count?: number;
  geometry_types?: string[] | Record<string, number>;
  bbox?: BBox | number[];
  mvt_capable?: boolean;
  raster_capable?: boolean;
  estimated_bytes?: number;
}
