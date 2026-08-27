import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  commitMapSpecDocument,
  getCommittedMapSpec,
  getMapSpecSessionCursor,
  setMapSpecSessionCursor,
} from '@/lib/mapspec/session-cursor';
import {
  commitComponentPatch,
  getComponentPlacementOverride,
  setComponentPlacementOverride,
} from '@/lib/mapspec/component-mutation';

vi.mock('@/lib/api/config', () => ({ API_BASE: 'http://localhost:8000' }));

const fetchMock = vi.fn();

async function flushAsync(rounds = 6): Promise<void> {
  for (let i = 0; i < rounds; i += 1) {
    await Promise.resolve();
  }
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe('commitComponentPatch（用户拖拽/缩放/折叠唯一提交通道，TE-P1-2）', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset();
    setMapSpecSessionCursor('sid-c', 11);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('posts patch_component with cursor revision and commits the returned spec', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      statusText: 'OK',
      text: () => Promise.resolve(JSON.stringify({
        success: true,
        mutation_revision: 12,
        mapspec: {
          layers: [],
          layout: {
            components: [{ id: 'north_arrow', placement: { mode: 'floating', x: 0.4, y: 0.4 } }],
          },
        },
      })),
    });

    await commitComponentPatch('north_arrow', { placement: { mode: 'floating', x: 0.4, y: 0.4 } as never });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const body = JSON.parse(fetchMock.mock.calls[0][1].body as string);
    expect(body.intent).toBe('patch_component');
    expect(body.component_id).toBe('north_arrow');
    expect(body.expected_revision).toBe(11);
    expect(getMapSpecSessionCursor().revision).toBe(12);
    const committed = getCommittedMapSpec() as { layout?: { components?: Array<{ id: string }> } };
    expect(committed?.layout?.components?.[0]?.id).toBe('north_arrow');
  });

  it('409 superseded converges silently: server spec committed, mismatched override dropped, no throw', async () => {
    // 本地 override（拖拽中）与服务端真相不一致 → superseded 后必须释放
    setComponentPlacementOverride('chart_panel', { mode: 'floating', x: 0.9, y: 0.9 } as never);

    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 409,
      statusText: 'Conflict',
      text: () => Promise.resolve(JSON.stringify({
        detail: {
          status: 'superseded',
          mutation_revision: 15,
          correction_hint: 'component was moved by a concurrent mutation',
          mapspec: {
            layers: [],
            layout: {
              components: [{ id: 'chart_panel', placement: { mode: 'floating', x: 0.1, y: 0.1 } }],
            },
          },
        },
      })),
    });

    await expect(
      commitComponentPatch('chart_panel', { placement: { mode: 'floating', x: 0.2, y: 0.2 } as never }),
    ).resolves.toBeUndefined();

    expect(getMapSpecSessionCursor().revision).toBe(15);
    // 服务端真相成为可见真相：压着的 override 被清
    expect(getComponentPlacementOverride('chart_panel')).toBeUndefined();
    const committed = getCommittedMapSpec() as { layout?: { components?: Array<{ id: string; placement?: { x?: number } }> } };
    expect(committed?.layout?.components?.[0]?.placement?.x).toBe(0.1);
  });

  it('non-409 errors propagate to the caller (FloatingChrome rollback contract)', async () => {
    fetchMock.mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Error',
      headers: { get: () => null },
      text: () => Promise.resolve(JSON.stringify({ detail: 'boom' })),
    });

    await expect(
      commitComponentPatch('scale_bar', { variant: 'boxed' }),
    ).rejects.toMatchObject({ status: 500 });
  });

  it('queued patches serialize: the second reads the revision advanced by the first', async () => {
    let rev = 11;
    fetchMock.mockImplementation(async () => {
      rev += 1;
      return {
        ok: true,
        status: 200,
        statusText: 'OK',
        text: () => Promise.resolve(JSON.stringify({
          success: true,
          mutation_revision: rev,
          mapspec: { layout: { components: [] } },
        })),
      };
    });

    await Promise.all([
      commitComponentPatch('north_arrow', { variant: 'arrow_simple' }),
      commitComponentPatch('scale_bar', { variant: 'boxed' }),
    ]);
    await flushAsync();

    expect(fetchMock).toHaveBeenCalledTimes(2);
    const bodies = fetchMock.mock.calls.map((c) => JSON.parse(c[1].body as string));
    expect(bodies[0].expected_revision).toBe(11);
    expect(bodies[1].expected_revision).toBe(12);
  });
});

describe('override reconcile（乐观 override 与 committed spec 收敛）', () => {
  beforeEach(() => {
    setMapSpecSessionCursor('sid-o', 1);
  });

  it('spec converging to the override placement clears it (MapSpec is the only truth)', async () => {
    const placement = { mode: 'floating' as const, x: 0.25, y: 0.75 };
    setComponentPlacementOverride('legend', placement);
    expect(getComponentPlacementOverride('legend')).toBeDefined();

    commitMapSpecDocument({
      layers: [],
      layout: { components: [{ id: 'legend', placement }] },
    } as never);

    // reconcileOverrides 由 mapspec live 订阅同步触发
    expect(getComponentPlacementOverride('legend')).toBeUndefined();
  });

  it('a differing server placement keeps the override until its own commit resolves', async () => {
    const placement = { mode: 'floating' as const, x: 0.8, y: 0.2 };
    setComponentPlacementOverride('colorbar', placement);

    commitMapSpecDocument({
      layers: [],
      layout: { components: [{ id: 'colorbar', placement: { mode: 'floating', x: 0.5, y: 0.5 } }] },
    } as never);

    expect(getComponentPlacementOverride('colorbar')).toBeDefined();
  });
});
