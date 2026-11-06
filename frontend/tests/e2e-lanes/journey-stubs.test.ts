/** The browser chooses the last matching route, not the most specific one. */
import { describe, expect, it, vi } from 'vitest';
import type { Page, Route, Request } from 'playwright/test';
import { defaultWorld, installJourneyStubs } from '../../e2e/fixtures/api-stubs';
import { TINY_PNG } from '../../e2e/fixtures/files';
import { TILE_PROVIDERS } from '../../lib/providers';

type Matcher = Parameters<Page['route']>[0];
type Handler = Parameters<Page['route']>[1];

async function responseFor(url: string) {
  const routes: Array<{ matcher: Matcher; handler: Handler }> = [];
  const page = {
    route: async (matcher: Matcher, handler: Handler) => {
      routes.push({ matcher, handler });
    },
  } as unknown as Page;
  const world = defaultWorld();
  await installJourneyStubs(page, world);
  const match = routes.reverse().find(({ matcher }) => {
    if (typeof matcher === 'string') return matcher === url;
    if (typeof matcher === 'function') return matcher(new URL(url));
    return matcher.test(url);
  });
  expect(match, `No route for ${url}`).toBeDefined();
  const fulfill = vi.fn();
  const request = {
    url: () => url,
    method: () => 'GET',
    headers: () => ({ origin: 'http://localhost:3310' }),
    postData: () => null,
  } as unknown as Request;
  await match!.handler({ request: () => request, fulfill } as unknown as Route, request);
  expect(fulfill).toHaveBeenCalledOnce();
  return { response: fulfill.mock.calls[0][0], world };
}

describe('journey mock route precedence', () => {
  it.each(TILE_PROVIDERS.filter((p) => p.type === 'vector'))(
    '$id returns a loadable offline style rather than the external fallback',
    async ({ url }) => {
      const { response } = await responseFor(url);
      expect(response.status).toBe(200);
      expect(response.contentType).toBe('application/json');
      const style = JSON.parse(response.body);
      expect(style.version).toBe(8);
      expect(style.sources).toEqual({});
      expect(style.layers).toEqual([
        { id: 'journey-background', type: 'background', paint: { 'background-color': '#e8eef4' } },
      ]);
      // No external glyph/sprite fetch is needed before the real map loads.
      expect(style.glyphs).toBeUndefined();
      expect(style.sprite).toBeUndefined();
    },
  );

  it('serves PNG tile bytes ahead of the external fallback', async () => {
    const { response } = await responseFor('https://a.basemaps.cartocdn.com/light_all/4/13/6@2x.png');
    expect(response.contentType).toBe('image/png');
    expect(response.body).toEqual(TINY_PNG);
  });

  it('serves empty PBF tiles with 204 ahead of the external fallback', async () => {
    const { response } = await responseFor('https://tiles.example.test/4/13/6.pbf');
    expect(response.status).toBe(204);
    expect(response.body).toBe('');
  });

  it.each(['http://localhost:8001', 'https://api.example.test'])(
    'keeps MVT API handlers and request recording active on %s', async (origin) => {
      const url = `${origin}/api/v1/layers/data/ref%3Aj1-hotspot/tiles/4/13/6.mvt`;
      const { response, world } = await responseFor(url);
      expect(response.status).toBe(200);
      expect(response.contentType).toBe('application/vnd.mapbox-vector-tile');
      expect(world.requests).toEqual([{ method: 'GET', path: new URL(url).pathname }]);
    },
  );

  it('serves product PNG downloads ahead of generic image and external routes', async () => {
    const url = 'https://api.example.test/api/v1/export/download/map.png';
    const { response, world } = await responseFor(url);
    expect(response.status).toBe(200);
    expect(response.contentType).toBe('image/png');
    expect(response.body).toEqual(TINY_PNG);
    // The generic PNG fixture returns the same bytes but bypasses the API.
    expect(world.requests).toEqual([{ method: 'GET', path: new URL(url).pathname }]);
  });

  it('still blanks unrelated external resources without network access', async () => {
    const { response } = await responseFor('https://unrelated.example.test/resource');
    expect(response).toEqual({ status: 200, contentType: 'text/plain', body: '' });
  });
});
