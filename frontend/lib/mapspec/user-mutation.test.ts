import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import { setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { toggleLayerAndCommit } from '@/lib/mapspec/user-mutation';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal('fetch', fetchMock);
  fetchMock.mockReset();
  useHudStore.getState().clearLayers();
  setMapSpecSessionCursor('sid-1', 3);
  useHudStore.getState().addLayer({
    id: 'L1',
    name: 'Schools',
    type: 'vector',
    visible: true,
    opacity: 1,
    group: 'analysis',
    source: { type: 'FeatureCollection', features: [] } as any,
    _mapspecLayerId: 'L1',
  } as any);
});

describe('toggleLayerAndCommit', () => {
  it('applies pending HUD visibility then hydrates from committed MapSpec', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: () => Promise.resolve(JSON.stringify({
        success: true,
        origin: 'user',
        mutation_revision: 4,
        mapspec: {
          layers: [{ id: 'L1', layout: { visibility: 'none' } }],
        },
      })),
    });

    await toggleLayerAndCommit('L1');

    expect(fetchMock).toHaveBeenCalledWith(
      expect.stringContaining('/api/v1/chat/sessions/sid-1/mapspec/mutations'),
      expect.objectContaining({ method: 'POST' }),
    );
    expect(useHudStore.getState().layers[0].visible).toBe(false);
  });

  it('rolls pending visibility back when the mutation is rejected', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: () => Promise.resolve(JSON.stringify({ detail: { status: 'superseded' } })),
    });

    await toggleLayerAndCommit('L1');

    expect(useHudStore.getState().layers[0].visible).toBe(true);
  });
});
