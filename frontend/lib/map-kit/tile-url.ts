/**
 * MVT tile URL builder — single template for all three construction sites
 * (SSE live path, session restore, spec mirror). Previously the same string
 * template was hand-duplicated ×3 (+#1112 review finding).
 *
 * V5-E content revision: `v=<content_revision>` is appended when the layer's
 * descriptor carries one. Overwrite/rollback keeps the same ref_id but bumps
 * the revision server-side, so the URL changes → the MapSpec reconciler sees
 * a source change → MapLibre re-adds the source and refetches tiles with a
 * cache-busting URL. This is the long-term fix for #1112 (same-ref content
 * mutation no longer depends on the 30s max-age mitigation). The backend
 * tile endpoints ignore the unknown query param.
 */
import { API_BASE } from '@/lib/api/config';

export function buildMvtTileUrl(
  refId: string,
  sessionId: string | undefined,
  contentRevision?: number | null
): string {
  const base = `${API_BASE}/api/v1/layers/data/${refId}/tiles/{z}/{x}/{y}.mvt?session_id=${sessionId}`;
  if (contentRevision === undefined || contentRevision === null || contentRevision <= 0) {
    return base;
  }
  return `${base}&v=${contentRevision}`;
}
