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
 *
 * #1725: sessionId is a hard precondition of the tile endpoint — the backend's
 * `require_owned_session` (SEC-08) refuses any request whose `session_id` does
 * not resolve to a live session. Interpolating an absent session produced the
 * literal `session_id=undefined`, which the endpoint rejects 100% of the time
 * while the HUD still reported the ref layer as loaded (silent blank map, no
 * error). A missing session now yields `''` instead of a doomed URL: every
 * consumer gates on a **truthy** `_tileUrl` (`layer-data.isMvtLayer`,
 * `renderer.isMvtSourceId`, `buildLoadPlan.hasTileUrl`, the layers-tab channel
 * chip), so an empty template reads as "not MVT-mounted" and the layer degrades
 * to the full-GeoJSON ref channel (which does authenticate) instead of mounting
 * a URL guaranteed to 404. Returning `''` rather than `null` keeps the return
 * type `string`, so the three construction sites need no branching of their own.
 */
import { API_BASE } from '@/lib/api/config';

/** 无会话时不产出 doomed URL（后端 require_owned_session 会全数拒绝）。 */
export const NO_MVT_TILE_URL = '';

export function buildMvtTileUrl(
  refId: string,
  sessionId: string | undefined,
  contentRevision?: number | null
): string {
  if (typeof sessionId !== 'string' || sessionId.trim() === '') {
    return NO_MVT_TILE_URL;
  }
  const base = `${API_BASE}/api/v1/layers/data/${refId}/tiles/{z}/{x}/{y}.mvt?session_id=${sessionId}`;
  if (contentRevision === undefined || contentRevision === null || contentRevision <= 0) {
    return base;
  }
  return `${base}&v=${contentRevision}`;
}
