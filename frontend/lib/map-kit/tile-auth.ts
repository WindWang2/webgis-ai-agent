import type { RequestParameters } from 'maplibre-gl';
import { API_BASE } from '@/lib/api/config';
import { getAccessToken } from '@/lib/auth/tokenStore';

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
 * - anonymous sessions → `X-Session-Token: <owner_token>`.
 * When both exist, both are sent — identical to `apiFetch`, and the backend
 * resolves whichever applies to the session.
 *
 * Credentials are ONLY attached to first-party URLs under `API_BASE`.
 * Third-party tile servers (OSM basemap, fonts, sprites) never see a session
 * credential — leaking the anonymous owner_token to a third party would be a
 * cross-origin secret exposure.
 */
export function buildTileTransformRequest(
  ownerToken: string | null,
): (url: string, resourceType?: string) => RequestParameters {
  return (url: string): RequestParameters => {
    if (!API_BASE || !url.startsWith(API_BASE)) {
      // Third-party or unknown host: never forward session credentials.
      return { url };
    }
    const headers: Record<string, string> = {};
    const accessToken = getAccessToken();
    if (accessToken) headers['Authorization'] = `Bearer ${accessToken}`;
    if (ownerToken) headers['X-Session-Token'] = ownerToken;
    return Object.keys(headers).length > 0 ? { url, headers } : { url };
  };
}
