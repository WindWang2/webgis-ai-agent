import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import { setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { setLayerOpacityAndCommit, toggleLayerAndCommit } from '@/lib/mapspec/user-mutation';

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

describe('setLayerOpacityAndCommit vs Agent upsert race', () => {
  it('drops pending opacity and hydrates the Agent MapSpec so a retry uses the new revision', async () => {
    fetchMock
      .mockResolvedValueOnce({
        ok: false,
        status: 409,
        statusText: 'Conflict',
        text: () => Promise.resolve(JSON.stringify({
          detail: {
            status: 'superseded',
            mutation_revision: 5,
            correction_hint: 'Re-read MapSpec and retry with the current mutation_revision.',
            mapspec: {
              layers: [{
                id: 'L1',
                layout: { visibility: 'visible' },
                paint: { opacity: 1, 'circle-opacity': 1, 'circle-color': '#ff0000' },
              }],
            },
          },
        })),
      })
      .mockResolvedValueOnce({
        ok: true,
        status: 200,
        statusText: 'OK',
        text: () => Promise.resolve(JSON.stringify({
          success: true,
          mutation_revision: 6,
          mapspec: {
            layers: [{
              id: 'L1',
              layout: { visibility: 'visible' },
              paint: { opacity: 0.4, 'circle-opacity': 0.4, 'circle-color': '#ff0000' },
            }],
          },
        })),
      });

    await setLayerOpacityAndCommit('L1', 0.4);

    expect(useHudStore.getState().layers[0].opacity).toBe(1);
    expect(getMapSpecSessionCursor().revision).toBe(5);

    await setLayerOpacityAndCommit('L1', 0.4);

    const second = JSON.parse(fetchMock.mock.calls[1][1].body as string);
    expect(second.expected_revision).toBe(5);
    expect(useHudStore.getState().layers[0].opacity).toBe(0.4);
    expect(getMapSpecSessionCursor().revision).toBe(6);
  });
});
