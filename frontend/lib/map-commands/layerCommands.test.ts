import { beforeEach, describe, expect, it, vi } from 'vitest';
import { layerCommands } from './layerCommands';
import type { MapCommandContext } from './types';
import * as renderer from '@/lib/map-kit/renderer';

vi.mock('@/lib/map-kit/renderer', () => ({
  updateLayerStyle: vi.fn(),
  addGeoJSONLayer: vi.fn(),
  removeLayer: vi.fn(),
}));

function context(layer: Record<string, unknown>, beforeVisible = false) {
  const updateLayer = vi.fn();
  const hud = { layers: [{ ...layer, visible: beforeVisible }], updateLayer };
  const ctx = {
    map: { getStyle: () => ({ layers: [{ id: `${layer.id}__point` }] }) },
    popAction: () => {},
    setDeferredPop: () => {},
    safePop: () => {},
    getHudState: () => hud,
    setSelectedBaseLayer: () => {},
    command: 'cartographic_runtime_repair',
    actionId: 'ma-carto-1',
    params: {
      mapspec_fingerprint: 'carto-sha256:current',
      observation_sequence: 4,
      repair_patches: [{
        layer_id: layer.id,
        mapspec_layer_id: 'result',
        before: { visible: false },
        desired: { visible: true },
        rules: ['RUNTIME_RESULT_VISIBILITY'],
      }],
    },
  } as unknown as MapCommandContext;
  return { ctx, updateLayer, hud };
}

describe('cartographic runtime repair command', () => {
  beforeEach(() => vi.clearAllMocks());

  it('applies an AUTO_SAFE presentation patch and stamps its action generation', () => {
    const { ctx, updateLayer } = context({
      id: 'runtime-result',
      _mapspecFingerprint: 'carto-sha256:current',
    });

    const result = layerCommands.cartographic_runtime_repair.run(ctx);

    expect(result).toEqual({
      status: 'succeeded',
      result: {
        confirmed: true,
        repair_action_id: 'ma-carto-1',
        observation_sequence: 4,
      },
    });
    expect(renderer.updateLayerStyle).toHaveBeenCalledWith(
      ctx.map,
      'runtime-result__point',
      expect.objectContaining({ visibility: 'visible' }),
    );
    expect(updateLayer).toHaveBeenCalledWith(
      'runtime-result',
      expect.objectContaining({
        visible: true,
        _mapspecFingerprint: 'carto-sha256:current',
        _mapspecRepairActionId: 'ma-carto-1',
      }),
    );
  });

  it('does not ACK a repair when no live MapLibre layer matched', () => {
    const { ctx, updateLayer } = context({
      id: 'runtime-result',
      _mapspecFingerprint: 'carto-sha256:current',
    });
    (ctx.map as any).getStyle = () => ({ layers: [] });

    const result = layerCommands.cartographic_runtime_repair.run(ctx);

    expect(result).toEqual({ status: 'failed', error: 'target_not_found' });
    expect(renderer.updateLayerStyle).not.toHaveBeenCalled();
    expect(updateLayer).not.toHaveBeenCalled();
  });

  it('does not overwrite a newer user change or stale MapSpec generation', () => {
    const { ctx, updateLayer, hud } = context({
      id: 'runtime-result',
      _mapspecFingerprint: 'carto-sha256:current',
    });
    hud.layers[0].visible = true;

    const result = layerCommands.cartographic_runtime_repair.run(ctx);

    expect(result).toEqual({ status: 'failed', error: 'superseded_by_user' });
    expect(renderer.updateLayerStyle).not.toHaveBeenCalled();
    expect(updateLayer).not.toHaveBeenCalled();
  });

  it('rejects a stale repair even when the user restored the old visible value', () => {
    const { ctx, updateLayer } = context({
      id: 'runtime-result',
      _mapspecFingerprint: 'carto-sha256:current',
      _intentGeneration: 8,
    });
    (ctx.params.repair_patches as any[])[0].before._intentGeneration = 7;

    const result = layerCommands.cartographic_runtime_repair.run(ctx);

    expect(result).toEqual({ status: 'failed', error: 'superseded_by_user' });
    expect(renderer.updateLayerStyle).not.toHaveBeenCalled();
    expect(updateLayer).not.toHaveBeenCalled();
  });
});
