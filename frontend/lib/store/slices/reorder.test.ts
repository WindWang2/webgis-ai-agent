import { describe, it, expect } from 'vitest';
import { createStore } from 'zustand/vanilla';
import { createLayersSlice } from './layersSlice';
import type { HudState } from '../hud-types';

type SliceState = ReturnType<typeof createLayersSlice>;
type Store = {
  getState: () => SliceState & HudState;
  setState: (partial: Partial<SliceState & HudState>) => void;
};

function makeStore(layers: any[]): Store {
  const store = createStore<any>()((...args) => ({
    ...createLayersSlice(args[0] as any, args[1] as any, args[2] as any),
  }));
  store.setState({ layers } as any);
  return store as unknown as Store;
}

describe('#1078(G-6) reorderLayers bookkeeping', () => {
  it('reorder bumps intent generation and re-tags only moved rows', () => {
    const mk = (id: string) => ({ id, name: id, type: 'vector', visible: true, opacity: 1, _intentGeneration: 1 } as any);
    const a = mk('a');
    const b = mk('b');
    const c = mk('c');
    const store = makeStore([a, b, c]);
    const genBefore = store.getState().layerIntentGeneration;
    store.getState().reorderLayers([a, c, b]);
    const s = store.getState();
    expect(s.layerIntentGeneration).toBe(genBefore + 1);
    const byId = Object.fromEntries(s.layers.map((l: any) => [l.id, l]));
    // 移动过的 b/c 重标；a 未动保留旧代
    expect(byId.a._intentGeneration).toBe(1);
    expect(byId.b._intentGeneration).toBe(genBefore + 1);
    expect(byId.c._intentGeneration).toBe(genBefore + 1);
    expect(s.layers.map((l: any) => l.id)).toEqual(['a', 'c', 'b']);
  });

  it('identical-array reorder is a no-op (no generation bump)', () => {
    const a = { id: 'a', name: 'a', type: 'vector', visible: true, opacity: 1, _intentGeneration: 1 } as any;
    const store = makeStore([a]);
    const genBefore = store.getState().layerIntentGeneration;
    const layersRef = store.getState().layers;
    store.getState().reorderLayers(layersRef);
    expect(store.getState().layerIntentGeneration).toBe(genBefore);
    expect(store.getState().layers).toBe(layersRef);
  });

});
