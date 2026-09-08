/**
 * B2/B3（workbench-v4）回归测试：
 * - B3：updateLayer({source:'server'}) 保留认证标签（服务端回声 ≠ 本地编辑）；
 * - B2：presentation_converged（不含鉴权条款）驱动状态派生 —— 用户 presentation
 *   编辑清空 fingerprint 后，地图与 desired 一致时不得再标「待同步」。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import { recordLayerEvidence, getLayerEvidence, clearLayerEvidence } from './render-evidence';
import { deriveLayerStatus } from './layer-status';
import type { Layer } from '@/lib/types/layer';

function makeLayer(patch: Partial<Layer> = {}): Layer {
  return {
    id: 'ly-b3',
    name: '测试层',
    type: 'vector',
    visible: true,
    opacity: 1,
    _mapspecFingerprint: 'gen-1',
    _mapspecLayerId: 'ly-b3',
    ...patch,
  } as Layer;
}

describe('B3 · updateLayer source=server 保留认证标签', () => {
  beforeEach(() => {
    useHudStore.getState().setLayers([makeLayer()]);
  });

  it('user 来源（缺省）清空 fingerprint —— 原语义不变', () => {
    useHudStore.getState().updateLayer('ly-b3', { visible: false });
    expect(useHudStore.getState().layers[0]._mapspecFingerprint).toBeUndefined();
  });

  it('server 来源保留 fingerprint（服务端回灌不得二次去失鉴权）', () => {
    useHudStore.getState().updateLayer('ly-b3', { visible: false }, { source: 'server' });
    const row = useHudStore.getState().layers[0];
    expect(row.visible).toBe(false);
    expect(row._mapspecFingerprint).toBe('gen-1');
  });

  it('server 来源且更新显式携带新 fingerprint 时以更新为准', () => {
    useHudStore.getState().updateLayer(
      'ly-b3',
      { visible: false, _mapspecFingerprint: 'gen-2' },
      { source: 'server' },
    );
    expect(useHudStore.getState().layers[0]._mapspecFingerprint).toBe('gen-2');
  });
});

describe('B2 · presentation_converged 驱动状态词表', () => {
  beforeEach(() => {
    clearLayerEvidence();
  });

  it('presentation_converged=true 且 style_converged=false（未鉴权）→ 收敛 → 非 stale', () => {
    recordLayerEvidence({
      layers: [{
        runtime_store_id: 'ly-b3',
        runtime_layer_count: 2,
        visible: true,
        source_converged: true,
        style_converged: false,
        presentation_converged: true,
      }],
    }, 7);
    const evidence = getLayerEvidence('ly-b3');
    expect(evidence?.converged).toBe(true);
    const status = deriveLayerStatus({
      layer: makeLayer({ _mapspecFingerprint: undefined }),
      evidence,
      currentRevision: 7,
    });
    expect(status).not.toBe('stale');
    expect(status).toBe('ready');
  });

  it('presentation_converged=false（runtime 真分歧）仍判 stale', () => {
    recordLayerEvidence({
      layers: [{
        runtime_store_id: 'ly-b3',
        runtime_layer_count: 0,
        visible: true,
        source_converged: true,
        style_converged: false,
        presentation_converged: false,
      }],
    }, 7);
    const evidence = getLayerEvidence('ly-b3');
    expect(deriveLayerStatus({
      layer: makeLayer({ _mapspecFingerprint: undefined }),
      evidence,
      currentRevision: 7,
    })).toBe('stale');
  });

  it('旧观测（无 presentation_converged 字段）回退 style_converged 原语义', () => {
    recordLayerEvidence({
      layers: [{
        runtime_store_id: 'ly-b3',
        runtime_layer_count: 2,
        visible: true,
        source_converged: true,
        style_converged: false,
      }],
    }, 7);
    const evidence = getLayerEvidence('ly-b3');
    expect(evidence?.converged).toBe(false);
    expect(deriveLayerStatus({
      layer: makeLayer(),
      evidence,
      currentRevision: 7,
    })).toBe('stale');
  });
});
