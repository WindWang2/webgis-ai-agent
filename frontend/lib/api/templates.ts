/**
 * Templates API client.
 *
 * F-FE-3 migration: list/create/delete go through the shared transport.
 * GETs go through the Fast Path (in-flight dedup + 5s LRU) — the gallery
 * fires `getTemplates` from multiple components on mount, and we don't want
 * 4-5 parallel GETs. The create path invalidates the list cache.
 *
 * Issue #464: GET /api/v1/templates returns the backend Page envelope
 * {items, total, limit, offset, has_more} (parseBody is a plain JSON.parse —
 * nothing unwraps it). `list` therefore normalizes the envelope (and a legacy
 * bare array, defensively) into a TemplatePage so consumers always see an
 * array-valued `items` plus the real `total` / `has_more` — the same
 * both-shapes handling as project.ts `asPage`.
 */

import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';

const TPL_LABEL = 'Template API error';

export type TemplateKind = 'basemap' | 'symbology' | 'layout' | 'thematic' | 'composite';
export type TemplateSource = 'builtin' | 'user';

/**
 * List DTO (summary=true, the default): the backend strips the heavy
 * `payload` JSON — only the detail endpoint (templatesApi.get) returns it.
 */
export interface TemplateSummary {
  id: string;
  org_id?: number | null;
  creator_id?: string | null;
  kind: TemplateKind;
  name: string;
  category?: string | null;
  keywords?: string[];
  description?: string | null;
  payload?: Record<string, unknown>;
  is_builtin: boolean;
  version: number;
  created_at?: string;
  updated_at?: string;
}

/** Detail DTO (GET /templates/{id}): the full template including payload. */
export type TemplateDetail = TemplateSummary & { payload: Record<string, unknown> };

/** Page envelope contract (app/schemas/pagination.py Page[T]). */
export interface TemplatePage {
  items: TemplateSummary[];
  total: number;
  limit: number;
  offset: number;
  has_more: boolean;
}

/** Normalize the Page envelope or a legacy bare array into a TemplatePage. */
function asTemplatePage(data: TemplatePage | TemplateSummary[] | undefined | null): TemplatePage {
  if (Array.isArray(data)) {
    return { items: data, total: data.length, limit: data.length || 50, offset: 0, has_more: false };
  }
  if (!data || typeof data !== 'object') {
    return { items: [], total: 0, limit: 50, offset: 0, has_more: false };
  }
  return {
    items: Array.isArray(data.items) ? data.items : [],
    total: data.total ?? 0,
    limit: data.limit ?? 50,
    offset: data.offset ?? 0,
    has_more: Boolean(data.has_more),
  };
}

export interface ListTemplatesParams {
  kind?: TemplateKind;
  q?: string;
  source?: TemplateSource;
  limit?: number;
  offset?: number;
  forceRefresh?: boolean;
  signal?: AbortSignal;
}

export const templatesApi = {
  async list(params?: ListTemplatesParams): Promise<TemplatePage> {
    const queryParams: Record<string, string | number> = {};
    if (params?.kind) queryParams.kind = params.kind;
    if (params?.q) queryParams.q = params.q;
    if (params?.source) queryParams.source = params.source;
    if (params?.limit !== undefined) queryParams.limit = params.limit;
    if (params?.offset !== undefined) queryParams.offset = params.offset;
    const result = await fastGet<TemplatePage | TemplateSummary[]>('/api/v1/templates', {
      params: queryParams,
      forceRefresh: params?.forceRefresh,
      signal: params?.signal,
      label: TPL_LABEL,
      // Template list is light; cache briefly so multi-component mounts dedupe.
      ttlMs: 5_000,
    });
    return asTemplatePage(result.data);
  },

  async get(
    id: string,
    opts?: { signal?: AbortSignal; forceRefresh?: boolean }
  ): Promise<TemplateDetail> {
    const result = await fastGet<TemplateDetail>(`/api/v1/templates/${encodeURIComponent(id)}`, {
      signal: opts?.signal,
      forceRefresh: opts?.forceRefresh,
      label: TPL_LABEL,
    });
    return result.data;
  },

  async create(req: {
    name: string;
    kind: TemplateKind;
    description?: string;
    keywords?: string[];
    payload: Record<string, unknown>;
    thumbnail_url?: string;
  }): Promise<TemplateSummary> {
    const out = await apiFetch<TemplateSummary>('/api/v1/templates', {
      method: 'POST',
      body: req,
      label: TPL_LABEL,
    });
    invalidateCache('/api/v1/templates');
    return out;
  },

  async delete(id: string): Promise<{ status: string; template_id: string }> {
    const out = await apiFetch<{ status: string; template_id: string }>(
      `/api/v1/templates/${encodeURIComponent(id)}`,
      { method: 'DELETE', label: TPL_LABEL }
    );
    invalidateCache('/api/v1/templates');
    return out;
  },
};
