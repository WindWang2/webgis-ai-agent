/**
 * Templates API client.
 *
 * F-FE-3 migration: list/create/delete go through the shared transport.
 * GETs go through the Fast Path (in-flight dedup + 5s LRU) — the gallery
 * fires `getTemplates` from multiple components on mount, and we don't want
 * 4-5 parallel GETs. The create path invalidates the list cache.
 *
 * The response shape is the same as the backend: a flat array of
 * `TemplateResponse` objects (no `{items, total}` envelope to keep
 * backward compatibility with the existing gallery consumer).
 */

import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';

const TPL_LABEL = 'Template API error';

export type TemplateKind = 'basemap' | 'symbology' | 'layout' | 'thematic' | 'composite';
export type TemplateSource = 'builtin' | 'user';

export interface TemplateSummary {
  id: string;
  org_id?: number | null;
  creator_id?: string | null;
  kind: TemplateKind;
  name: string;
  category?: string | null;
  keywords?: string[];
  description?: string | null;
  payload: Record<string, unknown>;
  is_builtin: boolean;
  version: number;
  created_at?: string;
  updated_at?: string;
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
  async list(params?: ListTemplatesParams): Promise<TemplateSummary[]> {
    const queryParams: Record<string, string | number> = {};
    if (params?.kind) queryParams.kind = params.kind;
    if (params?.q) queryParams.q = params.q;
    if (params?.source) queryParams.source = params.source;
    if (params?.limit !== undefined) queryParams.limit = params.limit;
    if (params?.offset !== undefined) queryParams.offset = params.offset;
    const result = await fastGet<TemplateSummary[]>('/api/v1/templates', {
      params: queryParams,
      forceRefresh: params?.forceRefresh,
      signal: params?.signal,
      label: TPL_LABEL,
      // Template list is light; cache briefly so multi-component mounts dedupe.
      ttlMs: 5_000,
    });
    return result.data;
  },

  async get(id: string, opts?: { signal?: AbortSignal; forceRefresh?: boolean }): Promise<TemplateSummary> {
    const result = await fastGet<TemplateSummary>(`/api/v1/templates/${encodeURIComponent(id)}`, {
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
