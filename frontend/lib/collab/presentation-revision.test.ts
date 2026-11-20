import { beforeEach, describe, expect, it } from 'vitest';
import { handleBusEnvelope } from './adopt';
import { noteLayerPresentationRevision, resetLayerPresentationRevisions } from './presentation-revision';
import { setMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { useHudStore } from '@/lib/store/useHudStore';

function envelope(revision: number, visible: boolean) {
  return {
    v: 1 as const,
    kind: 'presentation' as const,
    sid: 's1',
    seq: revision,
    ts: 't',
    data: { layerId: 'L', visible, revision },
  };
}

beforeEach(() => {
  resetLayerPresentationRevisions();
  setMapSpecSessionCursor('s1', 1, 'tok');
  useHudStore.setState({
    layers: [{ id: 'L', name: 'L', type: 'vector', visible: true, opacity: 1 } as never],
  });
});

describe('presentation revision gate', () => {
  it('drops a late older event on the same layer', () => {
    noteLayerPresentationRevision('L', 10);
    handleBusEnvelope(envelope(9, false));
    expect(useHudStore.getState().layers.find((layer) => layer.id === 'L')?.visible).toBe(true);
  });

  it('adopts a newer presentation revision', () => {
    noteLayerPresentationRevision('L', 4);
    handleBusEnvelope(envelope(5, false));
    expect(useHudStore.getState().layers.find((layer) => layer.id === 'L')?.visible).toBe(false);
  });
});
