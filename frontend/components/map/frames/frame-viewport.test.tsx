/**
 * G25 FrameViewport 生命周期测试（P4）：实例登记/释放、capture 有界失败、
 * context loss 代际重建、卸载清理（runtime dispose + 注册释放）。
 */
/* eslint-disable @typescript-eslint/no-require-imports -- vi.mock hoist 纪律 */
import { render, act, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { FrameViewport, type FrameViewportHandle } from './frame-viewport';
import { makeMockMaplibreMap } from '../../../test/__mocks__/maplibre-map';
import { mapInstanceRegistry } from './frame-instance-registry';
import type { CameraSnapshot } from '@/lib/cartography/frame-sync';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

const rmg = vi.hoisted(() => ({
  map: null as ReturnType<typeof makeMockMaplibreMap> | null,
  lostHandlers: new Set<() => void>(),
}));

vi.mock('react-map-gl/maplibre', () => {
  const React = require('react');
  const MapMock = React.forwardRef(function MapMock(props: any, ref: any) {
    React.useImperativeHandle(ref, () => ({ getMap: () => rmg.map }), []);
    React.useEffect(() => {
      const t = setTimeout(() => props.onLoad?.(), 0);
      return () => clearTimeout(t);
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);
    return React.createElement('div', { 'data-testid': 'frame-map-mock' });
  });
  return { default: MapMock };
});

const spec: MapSpec = {
  view: { center: [104, 30], zoom: 9 },
  sources: { s0: { type: 'geojson', inlineData: { type: 'FeatureCollection', features: [] } } },
  layers: [],
} as unknown as MapSpec;

const cam: CameraSnapshot = { center: [104, 30.6], zoom: 9, bearing: 0, pitch: 0 };

function mount() {
  const handleRef: { current: FrameViewportHandle | null } = { current: null };
  const onMountedFact = vi.fn();
  const utils = render(
    <FrameViewport
       
      ref={(h: any) => { handleRef.current = h; }}
      frameId="fp-1"
      effectiveSpec={spec}
      onMountedFact={onMountedFact}
      onCameraDraft={vi.fn()}
      mapStyle={{ version: 8, sources: {}, layers: [] }}
      initialCamera={cam}
      desiredSpecRevision={3}
      desiredDataRevision={7}
    />,
  );
  return { handleRef, onMountedFact, ...utils };
}

beforeEach(() => {
  mapInstanceRegistry.resetForTest();
  const map = makeMockMaplibreMap();
  const origOnce = map.once.bind(map);
  map.once = (((type: string, cb: (...a: unknown[]) => void) => {
    if (type === 'idle') {
      setTimeout(() => cb(), 0);
      return map;
    }
    return origOnce(type as never, cb as never);
  }) as typeof map.once);
  rmg.map = map as never;
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('FrameViewport 生命周期（P4）', () => {
  it('挂载登记预算槽位；卸载释放（A09/A26）', async () => {
    const { unmount } = mount();
    await waitFor(() => expect(mapInstanceRegistry.has('frame:fp-1')).toBe(true));
    expect(mapInstanceRegistry.liveCount()).toBe(1);
    unmount();
    expect(mapInstanceRegistry.has('frame:fp-1')).toBe(false);
    expect(mapInstanceRegistry.liveCount()).toBe(0);
  });

  it('capture：idle 后读取 live canvas dataUrl（成功）', async () => {
    const { handleRef, unmount } = mount();
    await waitFor(() => expect(handleRef.current).toBeTruthy());
    const cap = await handleRef.current!.capture();
    expect(cap).not.toBeNull();
    expect(cap!.dataUrl).toContain('data:image/png');
    expect(cap!.camera.zoom).toBeGreaterThan(0);
    unmount();
  });

  it('capture：map 缺席 → null（不伪造空白成功，P6 契约）', async () => {
    const { handleRef, unmount } = mount();
    await waitFor(() => expect(handleRef.current).toBeTruthy());
    const saved = rmg.map;
    rmg.map = null; // map 未就绪
    const cap = await handleRef.current!.capture();
    expect(cap).toBeNull();
    rmg.map = saved;
    unmount();
  });

  it('webglcontextlost（canvas 有 addEventListener 时）→ 代际重建 + 事实上报', async () => {
    // canvas-like 带 addEventListener 的假面（模拟真实浏览器）。
    const listeners = new Map<string, Set<(e: unknown) => void>>();
    const canvas = {
      width: 300, height: 200,
      addEventListener: (ev: string, h: (e: unknown) => void) => {
        if (!listeners.has(ev)) listeners.set(ev, new Set());
        listeners.get(ev)!.add(h);
      },
      removeEventListener: (ev: string, h: (e: unknown) => void) => listeners.get(ev)?.delete(h),
      toDataURL: () => 'data:image/png;base64,X',
    };
    const origGetCanvas = rmg.map!.getCanvas;
    (rmg.map as unknown as { getCanvas: () => unknown }).getCanvas = () => canvas;
    const onContextLost = vi.fn();
    const handleRef: { current: FrameViewportHandle | null } = { current: null };
    render(
      <FrameViewport
        ref={(h: any) => { handleRef.current = h; }}
        frameId="fp-1"
        effectiveSpec={spec}
        onMountedFact={vi.fn()}
        onCameraDraft={vi.fn()}
        mapStyle={{ version: 8, sources: {}, layers: [] }}
        initialCamera={cam}
        desiredSpecRevision={3}
        desiredDataRevision={7}
        onContextLost={onContextLost}
      />,
    );
    await waitFor(() => expect(listeners.get('webglcontextlost')?.size ?? 0).toBeGreaterThan(0));
    act(() => {
      for (const h of listeners.get('webglcontextlost')!) {
        h({ preventDefault: () => {} });
      }
    });
    await waitFor(() => expect(onContextLost).toHaveBeenCalledWith('fp-1', expect.any(Number)));
    (rmg.map as unknown as { getCanvas: () => unknown }).getCanvas = origGetCanvas;
  });

  it('连续挂载/卸载 30 次无注册残留（A26 有界）', async () => {
    for (let i = 0; i < 30; i++) {
      const { unmount } = mount();
      await waitFor(() => expect(mapInstanceRegistry.liveCount()).toBe(1));
      unmount();
    }
    expect(mapInstanceRegistry.liveCount()).toBe(0);
  });
});

