import { describe, it, expect, vi, beforeEach } from 'vitest';
import { API_BASE } from '@/lib/api/config';
import { getAccessToken } from '@/lib/auth/tokenStore';
import { buildTileTransformRequest } from './tile-auth';

// #514 regression: MapLibre tile/image fetches are browser-native and cannot
// carry headers, so the MVT tile endpoint (header-only require_owned_session)
// used to 404 for every >5000-feature ref layer. The transformRequest must
// inject the same credentials apiFetch sends — Bearer for logged-in sessions,
// X-Session-Token for anonymous — and ONLY for first-party URLs.
vi.mock('@/lib/auth/tokenStore', () => ({
  getAccessToken: vi.fn(),
}));

const mockGetAccessToken = vi.mocked(getAccessToken);

function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

describe('buildTileTransformRequest', () => {
  beforeEach(() => {
    mockGetAccessToken.mockReset();
    mockGetAccessToken.mockReturnValue(null);
  });

  it('injects X-Session-Token header for anonymous sessions with owner_token', () => {
    const transform = buildTileTransformRequest('anon-owner-token-abc');
    const url = apiUrl('/api/v1/layers/data/ref:geojson-123/tiles/4/5/6.mvt?session_id=sess-1');
    const result = transform(url, 'Tile');
    expect(result.url).toBe(url);
    expect(result.headers).toEqual({ 'X-Session-Token': 'anon-owner-token-abc' });
  });

  it('injects Authorization Bearer header for logged-in sessions', () => {
    mockGetAccessToken.mockReturnValue('jwt-access-1');
    const transform = buildTileTransformRequest(null);
    const url = apiUrl('/api/v1/layers/data/ref:geojson-123/tiles/4/5/6.mvt?session_id=sess-2');
    const result = transform(url, 'Tile');
    expect(result.headers).toEqual({ Authorization: 'Bearer jwt-access-1' });
  });

  it('sends both headers when both credentials exist (apiFetch parity)', () => {
    mockGetAccessToken.mockReturnValue('jwt-access-2');
    const transform = buildTileTransformRequest('anon-owner-token-2');
    const url = apiUrl('/api/v1/layers/data/ref:geojson-123/tiles/4/5/6.mvt?session_id=sess-3');
    const result = transform(url, 'Tile');
    expect(result.headers).toEqual({
      Authorization: 'Bearer jwt-access-2',
      'X-Session-Token': 'anon-owner-token-2',
    });
  });

  it('reads the access token fresh per request (rotated JWT picked up)', () => {
    mockGetAccessToken.mockReturnValueOnce('jwt-old');
    const transform = buildTileTransformRequest(null);
    const url = apiUrl('/api/v1/layers/data/ref:geojson-1/tiles/0/0/0.mvt?session_id=sess');
    expect(transform(url).headers?.Authorization).toBe('Bearer jwt-old');
    mockGetAccessToken.mockReturnValueOnce('jwt-refreshed');
    expect(transform(url).headers?.Authorization).toBe('Bearer jwt-refreshed');
  });

  it('never attaches credentials to third-party URLs (no owner_token leak)', () => {
    const transform = buildTileTransformRequest('anon-owner-token-secret');
    mockGetAccessToken.mockReturnValue('jwt-secret');
    const url = 'https://tile.openstreetmap.org/4/5/6.png';
    expect(transform(url, 'Tile')).toEqual({ url });
    expect(transform(url).headers).toBeUndefined();
  });

  it('never attaches credentials to unrelated same-host style URLs (fonts/sprites CDN)', () => {
    const transform = buildTileTransformRequest('anon-owner-token-secret');
    const url = 'https://fonts.example.com/glyphs/{fontstack}/{range}.pbf';
    expect(transform(url, 'Glyphs')).toEqual({ url });
    expect(transform(url).headers).toBeUndefined();
  });

  it('returns a bare request when no credentials are available', () => {
    const transform = buildTileTransformRequest(null);
    const url = apiUrl('/api/v1/layers/data/ref:geojson-1/tiles/0/0/0.mvt?session_id=sess');
    expect(transform(url, 'Tile')).toEqual({ url });
    expect(transform(url).headers).toBeUndefined();
  });
});
