/**
 * Layer API.
 *
 * F-FE-3 migration: getLayerTypes now flows through the Fast Path. Cookie-
 * bearing GET goes through apiFetch with credentials: 'include'. The two
 * `getMetadata` / `createAnalysisTask` methods that used to live here were
 * removed (their backend routes were deleted) — see the file history.
 */

import { fastGet } from './get-fast-path';

export interface LayerTypesResponse {
  layer_types: Array<{ type: string; description: string; formats: string[] }>;
  analysis_types: Array<{ type: string; description: string }>;
}

export const layerApi = {
  async getLayerTypes(opts?: { forceRefresh?: boolean; signal?: AbortSignal }): Promise<LayerTypesResponse> {
    const result = await fastGet<LayerTypesResponse>('/api/v1/layer-types', {
      forceRefresh: opts?.forceRefresh,
      signal: opts?.signal,
      credentials: 'include',
      label: 'Layer API error',
    });
    return result.data;
  },
};
