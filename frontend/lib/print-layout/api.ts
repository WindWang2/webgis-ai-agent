/** G34 REST 客户端（与 Pi 工具共用后端 print_layout_authoring.service）。 */
import { apiFetch } from '@/lib/api/transport';
import type { PrintLayoutV1 } from './contracts';

export interface PrintLayoutApiContext {
  sessionId: string;
  ownerToken: string | null;
  signal?: AbortSignal;
}

function q(ctx: PrintLayoutApiContext, extra = ''): string {
  return `?session_id=${encodeURIComponent(ctx.sessionId)}${extra}`;
}
function opts(ctx: PrintLayoutApiContext) {
  return { ownerToken: ctx.ownerToken, signal: ctx.signal, label: 'Print layout authoring' };
}

export interface Capabilities {
  success: boolean;
  schema_version: string;
  fonts: Array<{
    font_id: string; family: string; available: boolean;
    weights: string[]; supports_cjk: boolean; sha256: string;
  }>;
  font_asset_version: string;
  paper_profiles: Record<string, { width_mm: number; height_mm: number }>;
  operations: string[];
  component_kinds: string[];
  text_bindings: string[];
  budgets: Record<string, number>;
}

export interface InspectResult {
  success: boolean;
  status: 'configured' | 'not_configured' | 'blocked';
  plan?: PrintLayoutV1 | null;
  locks: string[];
  mapspec_revision: number;
  font_asset_version: string;
  validation?: { ok: boolean; issues: Array<Record<string, unknown>> };
  numbering?: { page_count: number; toc: unknown[]; toc_enabled: boolean };
}

export interface PagesResult {
  success: boolean;
  page_count: number;
  page_budget: number;
  planner_page_count: number;
  exceeds_budget: boolean;
  toc: Array<{ page_id: string; title: string; page_number: number }>;
  pages: Array<Record<string, unknown>>;
  disabled_pages: Array<Record<string, unknown>>;
}

export interface ProposeResult {
  success: boolean;
  status?: string;
  code?: string;
  message?: string;
  changes: Array<Record<string, unknown>>;
  skipped_locked: string[];
  locks: string[];
  adopted_values: Record<string, unknown>;
  validation: { ok: boolean; errors: Array<Record<string, unknown>>;
                warnings: Array<Record<string, unknown>> };
  issues?: Array<Record<string, unknown>>;
}

export interface ApplyResult {
  success: boolean;
  status?: string;
  code?: string;
  message?: string;
  committed: boolean;
  mutation_revision?: number;
  plan_fingerprint?: string;
  changes: Array<Record<string, unknown>>;
  skipped_locked: string[];
  validation: ProposeResult['validation'];
}

export interface PreviewResult {
  success: boolean;
  status?: string;
  code?: string;
  svg?: string;
  svg_sha256?: string;
  receipt?: Record<string, unknown>;
  warnings?: Array<Record<string, unknown>>;
  dpi?: number;
}

export interface ExportResult {
  success: boolean;
  status?: string;
  code?: string;
  format?: string;
  download_url?: string;
  manifest_url?: string;
  checksum?: string;
}

export function asPrintLayoutError(err: unknown): { code: string; message: string } {
  const body = (err as { body?: unknown } | null)?.body;
  const detail = body && typeof body === 'object'
    ? (body as Record<string, unknown>).detail ?? body
    : null;
  if (detail && typeof detail === 'object') {
    const d = detail as Record<string, unknown>;
    if (typeof d.code === 'string') {
      return { code: d.code, message: String(d.message ?? d.code) };
    }
  }
  return {
    code: 'PRINT_LAYOUT_REQUEST_FAILED',
    message: err instanceof Error ? err.message : String(err),
  };
}

export function fetchCapabilities(ctx: PrintLayoutApiContext) {
  return apiFetch<Capabilities>(`/api/v1/print-layout/capabilities${q(ctx)}`, opts(ctx));
}

export function fetchInspect(ctx: PrintLayoutApiContext, syncPlanner = false) {
  return apiFetch<InspectResult>(
    `/api/v1/print-layout/inspect${q(ctx, `&sync_planner=${syncPlanner}`)}`, opts(ctx));
}

export function fetchPages(ctx: PrintLayoutApiContext, driver = 'frames') {
  return apiFetch<PagesResult>(
    `/api/v1/print-layout/pages${q(ctx, `&driver=${encodeURIComponent(driver)}`)}`, opts(ctx));
}

export function postPropose(ctx: PrintLayoutApiContext, body: Record<string, unknown>) {
  return apiFetch<ProposeResult>(`/api/v1/print-layout/propose${q(ctx)}`, {
    ...opts(ctx), method: 'POST', body,
  });
}

export function postApply(ctx: PrintLayoutApiContext, body: Record<string, unknown>) {
  return apiFetch<ApplyResult>(`/api/v1/print-layout/apply${q(ctx)}`, {
    ...opts(ctx), method: 'POST', body,
  });
}

export function postPreview(ctx: PrintLayoutApiContext, body: Record<string, unknown>) {
  return apiFetch<PreviewResult>(`/api/v1/print-layout/preview${q(ctx)}`, {
    ...opts(ctx), method: 'POST', body,
  });
}

export function postExport(ctx: PrintLayoutApiContext, body: Record<string, unknown>) {
  return apiFetch<ExportResult>(`/api/v1/print-layout/export${q(ctx)}`, {
    ...opts(ctx), method: 'POST', body,
  });
}

export function postTemplate(ctx: PrintLayoutApiContext, body: Record<string, unknown>) {
  return apiFetch<Record<string, unknown>>(`/api/v1/print-layout/templates${q(ctx)}`, {
    ...opts(ctx), method: 'POST', body,
  });
}
