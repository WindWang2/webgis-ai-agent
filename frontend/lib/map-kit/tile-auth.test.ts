import { describe, it, expect, vi, beforeEach } from 'vitest';
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

// API_BASE is '' in production (Dockerfile build arg) and an absolute origin
// in dev. Make it mutable so both shapes are exercised.
let mockApiBase = 'http://localhost:8001';
vi.mock('@/lib/api/config', () => ({
  get API_BASE() {
    return mockApiBase;
  },
}));

const mockGetAccessToken = vi.mocked(getAccessToken);

/** The URL shape the frontend hands MapLibre for a ref layer's MVT tiles. */
function tileUrl(path: string): string {
  return `${mockApiBase}${path}`;
}

beforeEach(() => {
  mockApiBase = 'http://localhost:8001';
  mockGetAccessToken.mockReset();
  mockGetAccessToken.mockReturnValue(null);
});

describe('buildTileTransformRequest', () => {
  it('injects X-Session-Token header for anonymous sessions with owner_token', () => {
    const transform = buildTileTransformRequest(() => 'anon-owner-token-abc');
    const url = tileUrl('/api/v1/layers/data/ref:geojson-123/tiles/4/5/6.mvt?session_id=sess-1');
    const result = transform(url, 'Tile');
    expect(result.url).toBe(url);
    expect(result.headers).toEqual({ 'X-Session-Token': 'anon-owner-token-abc' });
  });

  it('injects Authorization Bearer header for logged-in sessions', () => {
    mockGetAccessToken.mockReturnValue('jwt-access-1');
    const transform = buildTileTransformRequest(() => null);
    const url = tileUrl('/api/v1/layers/data/ref:geojson-123/tiles/4/5/6.mvt?session_id=sess-2');
    const result = transform(url, 'Tile');
    expect(result.headers).toEqual({ Authorization: 'Bearer jwt-access-1' });
  });

  it('sends both headers when both credentials exist (apiFetch parity)', () => {
    mockGetAccessToken.mockReturnValue('jwt-access-2');
    const transform = buildTileTransformRequest(() => 'anon-owner-token-2');
    const url = tileUrl('/api/v1/layers/data/ref:geojson-123/tiles/4/5/6.mvt?session_id=sess-3');
    const result = transform(url, 'Tile');
    expect(result.headers).toEqual({
      Authorization: 'Bearer jwt-access-2',
      'X-Session-Token': 'anon-owner-token-2',
    });
  });

  it('reads the access token fresh per request (rotated JWT picked up)', () => {
    mockGetAccessToken.mockReturnValueOnce('jwt-old');
    const transform = buildTileTransformRequest(() => null);
    const url = tileUrl('/api/v1/layers/data/ref:geojson-1/tiles/0/0/0.mvt?session_id=sess');
    expect(transform(url).headers?.Authorization).toBe('Bearer jwt-old');
    mockGetAccessToken.mockReturnValueOnce('jwt-refreshed');
    expect(transform(url).headers?.Authorization).toBe('Bearer jwt-refreshed');
  });

  it('reads the owner_token LIVE per request — SSE arrival + session switch take effect', () => {
    // The token arrives via SSE AFTER the map is constructed (and MapLibre
    // never re-applies transformRequest), so the getter must be consulted on
    // every request rather than once at construction.
    let currentToken: string | null = null;
    const transform = buildTileTransformRequest(() => currentToken);
    const url = tileUrl('/api/v1/layers/data/ref:geojson-1/tiles/0/0/0.mvt?session_id=sess');

    expect(transform(url).headers).toBeUndefined();

    // SSE owner_token arrival while the map is already mounted.
    currentToken = 'anon-token-after-sse';
    expect(transform(url).headers?.['X-Session-Token']).toBe('anon-token-after-sse');

    // Session switch: the getter now returns the newly selected session's token.
    currentToken = 'anon-token-session-b';
    expect(transform(url).headers?.['X-Session-Token']).toBe('anon-token-session-b');

    // Logout / token cleared: header disappears again.
    currentToken = null;
    expect(transform(url).headers).toBeUndefined();
  });

  it('handles the production same-origin build (API_BASE === "") with relative URLs', () => {
    mockApiBase = '';
    mockGetAccessToken.mockReturnValue(null);
    const transform = buildTileTransformRequest(() => 'prod-owner-token');
    const url = '/api/v1/layers/data/ref:geojson-9/tiles/0/0/0.mvt?session_id=sess';
    const result = transform(url, 'Tile');
    expect(result.headers).toEqual({ 'X-Session-Token': 'prod-owner-token' });
  });

  it('never attaches credentials to third-party URLs (no owner_token leak)', () => {
    const transform = buildTileTransformRequest(() => 'anon-owner-token-secret');
    mockGetAccessToken.mockReturnValue('jwt-secret');
    const url = 'https://tile.openstreetmap.org/4/5/6.png';
    expect(transform(url, 'Tile')).toEqual({ url });
    expect(transform(url).headers).toBeUndefined();
  });

  it('never attaches credentials to hosts that merely share API_BASE as a prefix', () => {
    // http://api.example.com.evil.com must NOT match API_BASE http://api.example.com
    mockApiBase = 'http://api.example.com';
    const transform = buildTileTransformRequest(() => 'secret');
    const url = 'http://api.example.com.evil.com/api/v1/layers/data/ref:g/tiles/0/0/0.mvt?session_id=s';
    expect(transform(url).headers).toBeUndefined();
  });

  it('rejects protocol-relative third-party URLs when API_BASE is empty', () => {
    mockApiBase = '';
    const transform = buildTileTransformRequest(() => 'secret');
    const url = '//evil.example.com/api/v1/layers/data/ref:g/tiles/0/0/0.mvt?session_id=s';
    expect(transform(url, 'Tile')).toEqual({ url });
    expect(transform(url).headers).toBeUndefined();
  });

  it('returns a bare request when no credentials are available', () => {
    const transform = buildTileTransformRequest(() => null);
    const url = tileUrl('/api/v1/layers/data/ref:geojson-1/tiles/0/0/0.mvt?session_id=sess');
    expect(transform(url, 'Tile')).toEqual({ url });
    expect(transform(url).headers).toBeUndefined();
  });
});
