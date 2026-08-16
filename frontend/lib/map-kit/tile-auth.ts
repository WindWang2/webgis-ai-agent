import type { RequestParameters } from 'maplibre-gl';
import { getAccessToken } from '@/lib/auth/tokenStore';
import { isFirstPartyUrl } from '@/lib/api/first-party';

/**
 * Data-plane credential injection for MapLibre browser-native fetches.
 *
 * Tile/image requests made by MapLibre (vector tiles, raster tiles, image
 * sources) are plain browser fetches — they do NOT go through `apiFetch`, so
 * they can never carry the `Authorization` / `X-Session-Token` headers the
 * backend's `require_owned_session` (SEC-08) demands. Without this, every
 * >5000-feature ref layer that renders via the MVT tile endpoint 404s: the
 * URL only carries `?session_id=`, and the backend refuses header-less
 * requests for both anonymous (owner_token minted) and logged-in (Bearer)
 * sessions.
 *
 * Mirrors the credential injection `apiFetch` performs (`transport.ts`):
 * - logged-in sessions → `Authorization: Bearer <JWT>` (read fresh per
 *   request so a rotated access token is picked up),
 * - anonymous sessions → `X-Session-Token: <owner_token>` (read LIVE via the
 *   `getSessionToken` getter: the owner_token arrives via SSE after the map
 *   is constructed, and @vis.gl/react-maplibre never re-applies a
 *   transformRequest when props change — a snapshot captured at construction
 *   would be permanently stale and session switches would keep the old
 *   token).
 * When both exist, both are sent — identical to `apiFetch`, and the backend
 * resolves whichever applies to the session.
 *
 * Credentials are ONLY attached to first-party URLs (`isFirstPartyUrl`):
 * third-party tile servers (OSM basemap, fonts, sprites) never see a session
 * credential — leaking the anonymous owner_token to a third party would be a
 * cross-origin secret exposure. Handles the production same-origin build
 * (`API_BASE === ''` → relative URLs) and dev's absolute origin, comparing
 * origins exactly (never a prefix match).
 */
export function buildTileTransformRequest(
  getSessionToken: () => string | null,
): (url: string, resourceType?: string) => RequestParameters {
  return (url: string): RequestParameters => {
    if (!isFirstPartyUrl(url)) {
      // Third-party or unknown host: never forward session credentials.
      return { url };
    }
    const headers: Record<string, string> = {};
    const accessToken = getAccessToken();
    if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`;
    const ownerToken = getSessionToken();
    if (ownerToken) headers['X-Session-Token'] = ownerToken;
    return Object.keys(headers).length > 0 ? { url, headers } : { url };
  };
}
