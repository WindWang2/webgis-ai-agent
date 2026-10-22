/**
 * Composition Template Assets API client (G04).
 *
 * Backed by /api/v1/composition-template-assets (ADR-0214 留白收口):
 * builtin 4 families + scoped DB assets (private/org/project). Mutating
 * calls go through the shared transport; list reads use the Fast Path
 * (in-flight dedup + brief LRU) like templates.ts — the picker and the
 * save dialog both fire `list` on mount.
 */

import { apiFetch } from './transport';
import { fastGet, invalidateCache } from './get-fast-path';

const ASSET_LABEL = 'Composition template asset API error';

export type CompositionAssetStatus = 'draft' | 'published' | 'retired';
export type CompositionAssetVisibility = 'private' | 'org' | 'project';

/** List DTO — the backend projects a bounded summary per asset. */
export interface CompositionTemplateAssetSummary {
  asset_id: string;
  revision: number;
  status: CompositionAssetStatus;
  builtin: boolean;
  fingerprint: string;
  template_id: string;
  name: string;
  compatible_map_models?: string[];
  output_targets?: string[];
  visibility?: CompositionAssetVisibility;
  latest_revision?: number;
}

export interface CompositionTemplateAssetList {
  success: boolean;
  assets: CompositionTemplateAssetSummary[];
  asset_count: number;
  reason_codes: string[];
}

export interface CompositionAssetVersion {
  revision: number;
  status: CompositionAssetStatus;
  validation: string[];
  fingerprint: string;
  retired_at: string | null;
  retired_note: string;
  created_by: string | null;
  created_at: string | null;
}

export interface CompositionTemplateAssetDetail {
  success: boolean;
  asset_id: string;
  versions: CompositionAssetVersion[];
}

export interface CreateCompositionAssetRequest {
  session_id?: string;
  payload?: Record<string, unknown>;
  name?: string;
  description?: string;
  visibility?: CompositionAssetVisibility;
  project_id?: string;
  publish?: boolean;
}

export interface CreateCompositionAssetResult {
  success: boolean;
  asset_id: string;
  name?: string;
  visibility?: CompositionAssetVisibility;
  status: CompositionAssetStatus;
  revision: number;
  validation?: string[];
  fingerprint?: string;
}

function asList(data: unknown): CompositionTemplateAssetList {
  if (!data || typeof data !== 'object') {
    return { success: false, assets: [], asset_count: 0, reason_codes: [] };
  }
  const obj = data as Partial<CompositionTemplateAssetList>;
  return {
    success: obj.success === true,
    assets: Array.isArray(obj.assets) ? obj.assets : [],
    asset_count: typeof obj.asset_count === 'number' ? obj.asset_count : 0,
    reason_codes: Array.isArray(obj.reason_codes) ? obj.reason_codes : [],
  };
}

export const compositionTemplateAssetsApi = {
  async list(opts?: {
    includeRetired?: boolean;
    signal?: AbortSignal;
    forceRefresh?: boolean;
  }): Promise<CompositionTemplateAssetList> {
    const params: Record<string, string> = {};
    if (opts?.includeRetired) params.include_retired = 'true';
    const result = await fastGet<unknown>('/api/v1/composition-template-assets', {
      params,
      signal: opts?.signal,
      forceRefresh: opts?.forceRefresh,
      label: ASSET_LABEL,
      ttlMs: 5_000,
    });
    return asList(result.data);
  },

  async get(
    assetId: string,
    opts?: { signal?: AbortSignal }
  ): Promise<CompositionTemplateAssetDetail> {
    const result = await fastGet<CompositionTemplateAssetDetail>(
      `/api/v1/composition-template-assets/${encodeURIComponent(assetId)}`,
      { signal: opts?.signal, label: ASSET_LABEL }
    );
    return result.data;
  },

  async create(
    req: CreateCompositionAssetRequest
  ): Promise<CreateCompositionAssetResult> {
    const res = await apiFetch<CreateCompositionAssetResult>(
      '/api/v1/composition-template-assets',
      { method: 'POST', body: JSON.stringify(req) }
    );
    invalidateCache('/api/v1/composition-template-assets');
    return res;
  },

  async publish(assetId: string, revision: number): Promise<unknown> {
    const res = await apiFetch<unknown>(
      `/api/v1/composition-template-assets/${encodeURIComponent(assetId)}`
      + `/versions/${revision}/publish`,
      { method: 'POST', body: JSON.stringify({}) }
    );
    invalidateCache('/api/v1/composition-template-assets');
    return res;
  },

  async retire(assetId: string, note?: string): Promise<unknown> {
    const res = await apiFetch<unknown>(
      `/api/v1/composition-template-assets/${encodeURIComponent(assetId)}/retire`,
      { method: 'POST', body: JSON.stringify({ note: note ?? '' }) }
    );
    invalidateCache('/api/v1/composition-template-assets');
    return res;
  },

  async restore(assetId: string, revision: number): Promise<unknown> {
    const res = await apiFetch<unknown>(
      `/api/v1/composition-template-assets/${encodeURIComponent(assetId)}`
      + `/restore/${revision}`,
      { method: 'POST', body: JSON.stringify({}) }
    );
    invalidateCache('/api/v1/composition-template-assets');
    return res;
  },
};
