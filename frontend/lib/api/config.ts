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
export const WS_BASE = process.env.NEXT_PUBLIC_WS_URL ?? 'ws://localhost:8001';
