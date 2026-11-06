/** Real controller + parent passive sync + manager; no WebGL required. */
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useEffect, useSyncExternalStore } from 'react';
import type { MapSpec } from '@/lib/mapspec-compiler/types.generated';
import { buildGlbFixture } from '@/lib/map-kit/scene3d/__fixtures__/glb';
import { getScene3dManager } from '@/lib/map-kit/scene3d/asset-manager';

const state = vi.hoisted(() => ({
  spec: null as MapSpec | null,
  generation: 0,
  listeners: new Set<() => void>(),
}));
vi.mock('@/lib/mapspec/session-cursor', () => ({
  getCommittedMapSpec: () => state.spec,
  getMapSpecLiveGeneration: () => state.generation,
  subscribeMapSpecLive: (cb: () => void) => {
    state.listeners.add(cb);
    return () => state.listeners.delete(cb);
  },
}));
vi.mock('@/lib/mapspec/scene-mutation', () => ({
  commitUserScene: vi.fn(async () => ({ outcome: 'committed' })),
}));
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: { getState: () => ({ is3D: true, setIs3D: vi.fn() }) },
}));
vi.mock('@/lib/i18n/useT', () => ({ useT: () => (key: string) => key }));
vi.mock('@/lib/map-kit/renderer', async () => {
  const scene3d = await import('@/lib/map-kit/scene3d');
  return {
    scene3dSetAssetPresentation: scene3d.scene3dSetAssetPresentation,
    scene3dApplyLightingPreset: scene3d.scene3dApplyLightingPreset,
  };
});
import { SceneController } from './scene-controller';

function makeMap() {
  const layers = new Set<string>();
  return {
    layers,
    addLayer: (layer: { id: string }) => { layers.add(layer.id); },
    removeLayer: (id: string) => { layers.delete(id); },
    getLayer: (id: string) => layers.has(id) ? { id } : undefined,
    getPitch: () => 0,
    on: () => {}, off: () => {}, triggerRepaint: () => {},
  };
}

function commit(revision: number, mode: '2d' | '3d' = '3d') {
  state.spec = {
    version: '1.7', layers: [],
    sources: { m: { type: 'model3d', format: 'glb', url: 'https://assets.test/m.glb', content_revision: revision } },
    scene: { mode, assets: [{ id: 'campus', source: 'm', visible: true, placement: { anchor: [104 + revision / 100, 30] } }] },
  } as MapSpec;
  state.generation += 1;
  state.listeners.forEach((listener) => listener());
}

/** Like MapPanel: the child renders before the parent's scene sync effect. */
function Parent({ map, sessionId }: { map: ReturnType<typeof makeMap>; sessionId: string }) {
  const generation = useSyncExternalStore((cb) => {
    state.listeners.add(cb);
    return () => { state.listeners.delete(cb); };
  }, () => state.generation, () => state.generation);
  useEffect(() => { getScene3dManager(map).sync(state.spec, sessionId); }, [map, generation, sessionId]);
  return <SceneController sessionId={sessionId} getMap={() => map} />;
}

let map: ReturnType<typeof makeMap>;
beforeEach(() => {
  map = makeMap();
  vi.stubGlobal('fetch', vi.fn(async () => new Response(buildGlbFixture({ triangles: 2 }), { status: 200 })));
});
afterEach(() => {
  cleanup();
  getScene3dManager(map).dispose();
  vi.unstubAllGlobals();
});
async function mounted() {
  await vi.waitFor(() => expect(getScene3dManager(map).describe()[0]?.state).toBe('mounted'));
}
function openPanel() { fireEvent.click(screen.getByRole('button', { name: 'sceneController.title' })); }

it('keeps a hidden asset hidden when the parent remounts a changed source and placement', async () => {
  commit(1);
  render(<Parent map={map} sessionId="session-a" />);
  await mounted();
  openPanel();
  fireEvent.click(screen.getByLabelText('campus'));
  expect(getScene3dManager(map).describe()[0].visibleEffective).toBe(false);
  act(() => commit(2));
  await mounted();
  expect((screen.getByLabelText('campus') as HTMLInputElement).checked).toBe(false);
  expect(getScene3dManager(map).describe()[0].visibleEffective).toBe(false);
});

it('keeps opacity through source remount, a style sweep and context restoration', async () => {
  commit(1);
  render(<Parent map={map} sessionId="session-a" />);
  await mounted();
  openPanel();
  const sliders = screen.getAllByRole('slider') as HTMLInputElement[];
  const opacity = sliders.find((slider) => slider.min === '0.05')!;
  fireEvent.change(opacity, { target: { value: '0.35' } });
  const manager = getScene3dManager(map);
  const readOpacity = () => {
    const assets = manager as unknown as { mounted: Map<string, { singleModel: { root: { traverse: (visit: (obj: { isMesh?: boolean; material?: { opacity: number } }) => void) => void } } }> };
    const values: number[] = [];
    assets.mounted.get('campus')!.singleModel.root.traverse((obj) => { if (obj.isMesh) values.push(obj.material!.opacity); });
    return values;
  };
  act(() => commit(2));
  await mounted();
  expect(readOpacity()).toEqual([0.35]);
  map.layers.clear();
  manager.sync(state.spec, 'session-a');
  await mounted();
  expect(readOpacity()).toEqual([0.35]);
  manager.handleContextLost();
  manager.handleContextRestored(state.spec);
  await mounted();
  expect(readOpacity()).toEqual([0.35]);
});

it('retains overrides through 2d mode, then clears them for the next session with the same asset id', async () => {
  commit(1);
  const view = render(<Parent map={map} sessionId="session-a" />);
  await mounted();
  openPanel();
  fireEvent.click(screen.getByLabelText('campus'));
  act(() => commit(1, '2d'));
  expect(getScene3dManager(map).describe()).toEqual([]);
  act(() => commit(1));
  await mounted();
  expect(getScene3dManager(map).describe()[0].visibleEffective).toBe(false);
  view.rerender(<Parent map={map} sessionId="session-b" />);
  await mounted();
  expect((screen.getByLabelText('campus') as HTMLInputElement).checked).toBe(true);
  expect(getScene3dManager(map).describe()[0].visibleEffective).toBe(true);
});
