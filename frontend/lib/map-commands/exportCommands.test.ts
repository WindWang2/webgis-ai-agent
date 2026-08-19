import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { makeMockMaplibreMap } from '@/test/__mocks__/maplibre-map';
import { exportCommands } from './exportCommands';
import type { MapCommandContext } from './types';

// export_map's run dynamically imports the heavy exporter engine; mock it so the
// V3 promise-returning export path can be exercised without the real engine.
vi.mock('@/lib/map-kit/exporter', () => ({
  MapExporterEngine: { export: vi.fn(async () => ({ ok: true })) },
}));

function makeCtx(map: any, params: Record<string, unknown> = {}): MapCommandContext {
  return {
    map,
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => ({}),
    setSelectedBaseLayer: () => {},
    command: 'export_map',
    params,
  } as MapCommandContext;
}

describe('exportCommands export_map (V3 Promise<MapCommandResult> contract)', () => {
  beforeEach(async () => {
    vi.useFakeTimers();
    const { MapExporterEngine } = await import('@/lib/map-kit/exporter');
    (MapExporterEngine.export as ReturnType<typeof vi.fn>).mockReset();
    (MapExporterEngine.export as ReturnType<typeof vi.fn>).mockResolvedValue({ ok: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('succeeds only after the render callback finishes the export work', async () => {
    const map = makeMockMaplibreMap();
    const promise = exportCommands.export_map.run(makeCtx(map, { format: 'png' }));

    // not settled until the render callback ran the full export pipeline
    map._fire('render');
    await vi.advanceTimersByTimeAsync(0); // flush the async render callback
    await expect(promise).resolves.toEqual({ status: 'succeeded' });
  });

  it('resolves failed export_failed when the engine reports a failure', async () => {
    const { MapExporterEngine } = await import('@/lib/map-kit/exporter');
    (MapExporterEngine.export as ReturnType<typeof vi.fn>).mockResolvedValueOnce({
      ok: false,
      error: 'canvas busy',
    });

    const map = makeMockMaplibreMap();
    const promise = exportCommands.export_map.run(makeCtx(map, {}));

    map._fire('render');
    await vi.advanceTimersByTimeAsync(0);
    await expect(promise).resolves.toEqual({ status: 'failed', error: 'export_failed' });
  });

  it('resolves failed timeout if render never fires (queue cannot stall)', async () => {
    const map = makeMockMaplibreMap();
    const promise = exportCommands.export_map.run(makeCtx(map, {}));

    await vi.advanceTimersByTimeAsync(30_000);
    await expect(promise).resolves.toEqual({ status: 'failed', error: 'timeout' });
  });

  it('ignores a render callback that arrives after the timeout (first settle wins)', async () => {
    const { MapExporterEngine } = await import('@/lib/map-kit/exporter');
    const exportFn = MapExporterEngine.export as ReturnType<typeof vi.fn>;
    const setPendingSystemMessage = vi.fn();
    exportFn.mockImplementation(async () => {
      setPendingSystemMessage('[系统通知] 专题地图已成功排版合成');
      return { ok: true };
    });

    const map = makeMockMaplibreMap();
    const promise = exportCommands.export_map.run({
      ...makeCtx(map, {}),
      getHudState: () => ({ setPendingSystemMessage }),
    } as MapCommandContext);

    await vi.advanceTimersByTimeAsync(30_000);
    map._fire('render'); // late render must not flip the already-settled timeout
    await vi.advanceTimersByTimeAsync(0);
    await Promise.resolve();
    await Promise.resolve();

    await expect(promise).resolves.toEqual({ status: 'failed', error: 'timeout' });
    // #618 item 21: timeout already acked failure — do not run the export
    // pipeline or emit a user-facing success system message.
    expect(exportFn).not.toHaveBeenCalled();
    expect(setPendingSystemMessage).not.toHaveBeenCalled();
  });
});
