import { describe, it, expect, beforeEach, vi } from 'vitest';
import { buildMvtTileUrl, NO_MVT_TILE_URL } from './tile-url';

// #1725 regression: the tile endpoint's require_owned_session (SEC-08) rejects
// every request whose session_id does not resolve to a live session. The
// builder used to interpolate whatever it got, so an absent session produced
// the literal `session_id=undefined` — a URL the backend refuses 100% of the
// time while the HUD still reported the ref layer as loaded (silent blank map,
// no error). A missing session must now yield the empty template, which every
// consumer's truthiness gate (`isMvtLayer` / `renderer.isMvtSourceId` /
// `buildLoadPlan.hasTileUrl`) reads as "not MVT-mounted" — the layer then
// degrades to the authenticated full-GeoJSON ref channel instead.

let mockApiBase = 'http://localhost:8001';
vi.mock('@/lib/api/config', () => ({
  get API_BASE() {
    return mockApiBase;
  },
}));

beforeEach(() => {
  mockApiBase = 'http://localhost:8001';
});

describe('buildMvtTileUrl', () => {
  it('builds the session-scoped template when the session id is present', () => {
    expect(buildMvtTileUrl('ref:geojson-1', 'sess-abc')).toBe(
      'http://localhost:8001/api/v1/layers/data/ref:geojson-1/tiles/{z}/{x}/{y}.mvt?session_id=sess-abc',
    );
  });

  it('appends the content revision as a cache-busting param (#1112)', () => {
    expect(buildMvtTileUrl('ref:geojson-1', 'sess-abc', 7)).toBe(
      'http://localhost:8001/api/v1/layers/data/ref:geojson-1/tiles/{z}/{x}/{y}.mvt?session_id=sess-abc&v=7',
    );
    // 0 / null / undefined revisions add no param (unchanged contract).
    expect(buildMvtTileUrl('ref:geojson-1', 'sess-abc', 0)).not.toContain('&v=');
    expect(buildMvtTileUrl('ref:geojson-1', 'sess-abc', null)).not.toContain('&v=');
    expect(buildMvtTileUrl('ref:geojson-1', 'sess-abc', undefined)).not.toContain('&v=');
  });

  // #1725: the bug — never emit a URL the backend is guaranteed to reject.
  it('returns no URL when sessionId is undefined', () => {
    expect(buildMvtTileUrl('ref:geojson-1', undefined)).toBe(NO_MVT_TILE_URL);
    expect(buildMvtTileUrl('ref:geojson-1', undefined, 3)).toBe(NO_MVT_TILE_URL);
  });

  it('returns no URL for an empty or whitespace-only sessionId', () => {
    expect(buildMvtTileUrl('ref:geojson-1', '')).toBe(NO_MVT_TILE_URL);
    expect(buildMvtTileUrl('ref:geojson-1', '   ')).toBe(NO_MVT_TILE_URL);
  });

  it('never interpolates the literal "undefined" into the query string', () => {
    for (const url of [
      buildMvtTileUrl('ref:geojson-1', undefined),
      buildMvtTileUrl('ref:geojson-1', ''),
      buildMvtTileUrl('ref:geojson-1', null as unknown as string | undefined),
    ]) {
      expect(url).not.toContain('session_id=undefined');
      expect(url).toBe('');
    }
  });
});
