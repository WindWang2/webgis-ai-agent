/**
 * Centralized API configuration
 * All API and WebSocket URLs should import from this module.
 *
 * Transport goal E-F-2: NEXT_PUBLIC_* vars are inlined at BUILD time. The
 * Dockerfiles pass NEXT_PUBLIC_API_URL as a build arg defaulting to "" so a
 * production bundle uses same-origin (relative) URLs behind the reverse proxy.
 * We use ?? (not ||) so an explicitly-built empty string wins over the dev
 * fallback — with ||, "" would fall through to localhost:8001 and break prod.
 * Local `npm run dev` (var unset) still gets http://localhost:8001.
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8001';
export const WS_BASE = process.env.NEXT_PUBLIC_WS_URL ?? deriveWsFromApiBase(API_BASE);

function deriveWsFromApiBase(apiBase: string): string {
  // Unset WS var: follow the API base (dev http://localhost:8001 → ws://…).
  // Same-origin API ('') → '' here; getWsBase() resolves it at runtime.
  if (!apiBase) return '';
  return apiBase.replace(/^http(s?):\/\//i, 'ws$1://');
}

/**
 * F-04: WebSocket base resolved at CALL time (never at module load — SSR has
 * no window). An empty build-time WS_BASE (production same-origin build, the
 * Dockerfiles default NEXT_PUBLIC_WS_URL="") derives `ws(s)://<page host>`
 * from window.location so collab goes through the reverse proxy's
 * /api/v1/ws/ location (and https pages get wss://, not mixed-content ws://).
 */
export function getWsBase(): string {
  if (WS_BASE) return WS_BASE;
  if (typeof window !== 'undefined' && window.location?.host) {
    const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${proto}//${window.location.host}`;
  }
  return '';
}
