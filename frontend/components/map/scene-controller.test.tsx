/**
 * G3D05（ADR-0275 D5）：场景控制器测试 —— 人机同语义、证据门、
 * reduced-motion、a11y。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach } from 'vitest';

// —— mocks（模式对齐 map-panel.test / scene-mutation.test）———————
let committed: Record<string, unknown> | null = null;
let liveGeneration = 0;
const listeners = new Set<() => void>();
function commitSpec(spec: Record<string, unknown> | null) {
  committed = spec;
  liveGeneration += 1;
  listeners.forEach((l) => l());
}

vi.mock('@/lib/mapspec/session-cursor', () => ({
  getCommittedMapSpec: () => committed,
  getMapSpecLiveGeneration: () => liveGeneration,
  subscribeMapSpecLive: (cb: () => void) => {
    listeners.add(cb);
    return () => listeners.delete(cb);
  },
}));

const commitUserScene = vi.fn();
vi.mock('@/lib/mapspec/scene-mutation', () => ({
  commitUserScene: (...args: unknown[]) => commitUserScene(...args),
}));

const setIs3D = vi.fn();
let is3D = false;
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: {
    getState: () => ({ is3D, setIs3D: (v: boolean) => { is3D = v; setIs3D(v); } }),
  },
}));

vi.mock('@/lib/i18n/useT', () => ({
  useT: () => (key: string) => key,
}));

vi.mock('@/lib/utils/logger', () => ({
  devOnly: () => undefined,
}));

// G3DV2-B M5：本地表象控制缝（renderer 门面）—— 单测打桩隔离 THREE 链。
const scene3dSetAssetPresentation = vi.fn<(map: unknown, assetId: string, patch: { visible?: boolean; opacity?: number }) => boolean>(() => true);
const scene3dApplyLightingPreset = vi.fn<(map: unknown, preset: string) => boolean>(() => true);
vi.mock('@/lib/map-kit/renderer', () => ({
  scene3dSetAssetPresentation: (...args: Parameters<typeof scene3dSetAssetPresentation>) =>
    scene3dSetAssetPresentation(...args),
  scene3dApplyLightingPreset: (...args: Parameters<typeof scene3dApplyLightingPreset>) =>
    scene3dApplyLightingPreset(...args),
}));

import { SceneController } from './scene-controller';

const openPanel = () => {
  fireEvent.click(screen.getByRole('button', { name: 'sceneController.title' }));
};

describe('SceneController', () => {
  beforeEach(() => {
    committed = null;
    is3D = false;
    commitUserScene.mockReset().mockResolvedValue({ outcome: 'committed' });
    setIs3D.mockClear();
  });
  afterEach(() => cleanup());

  it('renders trigger reflecting committed scene mode', () => {
    commitSpec({ scene: { mode: '3d' } });
    render(<SceneController />);
    expect(screen.getByRole('button', { name: 'sceneController.title' })).toBeTruthy();
  });

  it('mode switch commits scene payload and flips is3D', async () => {
    commitSpec({
      scene: { mode: '2d', camera: { pitch: 0, bearing: 0 } },
      sources: { dem: { type: 'raster-dem' } },
    });
    render(<SceneController />);
    openPanel();
    fireEvent.click(screen.getByRole('radio', { name: 'sceneController.mode_3d' }));
    await vi.waitFor(() => expect(commitUserScene).toHaveBeenCalledTimes(1));
    const payload = commitUserScene.mock.calls[0][0];
    expect(payload.mode).toBe('3d');
    expect(payload.camera.pitch).toBe(50); // 2d→3d 缺省安全 pitch
    expect(setIs3D).toHaveBeenCalledWith(true);
  });

  it('switching back to 2d clears pitch and is3D', async () => {
    commitSpec({
      scene: { mode: '3d', camera: { pitch: 55, bearing: 30 } },
      sources: {},
    });
    is3D = true;
    render(<SceneController />);
    openPanel();
    fireEvent.click(screen.getByRole('radio', { name: 'sceneController.mode_2d' }));
    await vi.waitFor(() => expect(commitUserScene).toHaveBeenCalled());
    const payload = commitUserScene.mock.calls[0][0];
    expect(payload.mode).toBe('2d');
    expect(payload.camera.pitch).toBe(0);
    expect(payload.camera.bearing).toBe(0);
    expect(setIs3D).toHaveBeenCalledWith(false);
  });

  it('exaggeration slider disabled without raster-dem evidence', () => {
    commitSpec({ scene: { mode: '3d', terrain: { exaggeration: 1.5 } }, sources: {} });
    render(<SceneController />);
    openPanel();
    const slider = screen.getByLabelText(/sceneController.exaggeration/, { selector: 'input' });
    expect(slider).toBeTruthy();
    expect((slider as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText('sceneController.exaggerationDisabled')).toBeTruthy();
  });

  it('exaggeration change with evidence commits terrain block', async () => {
    commitSpec({
      scene: { mode: '3d', terrain: { source: 'dem', exaggeration: 1 } },
      sources: { dem: { type: 'raster-dem' } },
    });
    render(<SceneController />);
    openPanel();
    const slider = screen.getByLabelText(/sceneController.exaggeration/, { selector: 'input' });
    fireEvent.change(slider, { target: { value: '2.5' } });
    await vi.waitFor(() => expect(commitUserScene).toHaveBeenCalled());
    const payload = commitUserScene.mock.calls[0][0];
    expect(payload.terrain.exaggeration).toBe(2.5);
    expect(payload.terrain.source).toBe('dem'); // 保留既有源声明
  });

  it('pitch stepper commits camera and clamps at 85', async () => {
    commitSpec({ scene: { mode: '3d', camera: { pitch: 82, bearing: 0 } }, sources: {} });
    render(<SceneController />);
    openPanel();
    fireEvent.click(screen.getByRole('button', { name: 'sceneController.pitchUp' }));
    await vi.waitFor(() => expect(commitUserScene).toHaveBeenCalled());
    expect(commitUserScene.mock.calls[0][0].camera.pitch).toBe(85);
  });

  it('discloses vertical datum and undeclared state', () => {
    commitSpec({
      scene: {
        mode: '3d',
        terrain: { source: 'dem', vertical_unit: 'm' },
      },
      sources: { dem: { type: 'raster-dem' } },
    });
    render(<SceneController />);
    openPanel();
    expect(screen.getByText(/sceneController.datumUndeclared/)).toBeTruthy();
    expect(screen.getByText(/sceneController.unitSuffix/)).toBeTruthy();
  });

  it('dialog has radiogroup and expanded state on trigger', () => {
    commitSpec({ scene: { mode: '2d' }, sources: {} });
    render(<SceneController />);
    const trigger = screen.getByRole('button', { name: 'sceneController.title' });
    expect(trigger.getAttribute('aria-expanded')).toBe('false');
    openPanel();
    expect(trigger.getAttribute('aria-expanded')).toBe('true');
    // G3DV2-B M5：模式组 + 光照组两个 radiogroup —— 按 aria-label 精确定位。
    expect(screen.getByRole('radiogroup', { name: 'sceneController.mode' })).toBeTruthy();
    expect(screen.getByRole('radiogroup', { name: 'sceneController.lighting' })).toBeTruthy();
    expect(screen.getByRole('dialog')).toBeTruthy();
  });

  it('renders per-asset presentation controls and drives renderer seam (M5)', () => {
    commitSpec({
      scene: {
        mode: '3d',
        assets: [{ id: 'campus', source: 'model-a', visible: true }],
      },
      sources: {},
    });
    const getMap = () => ({ getCenter: () => ({ lng: 104, lat: 30 }) }) as never;
    render(<SceneController getMap={getMap} />);
    openPanel();
    const checkbox = screen.getByRole('checkbox');
    expect(checkbox.getAttribute('id')).toBe('scene-asset-campus');
    fireEvent.click(checkbox); // 本地隐藏
    expect(scene3dSetAssetPresentation).toHaveBeenCalledWith(
      expect.anything(), 'campus', { visible: false },
    );
    // 光照预设 → 渲染缝
    fireEvent.click(screen.getByRole('radio', { name: 'sceneController.lighting_soft' }));
    expect(scene3dApplyLightingPreset).toHaveBeenCalledWith(expect.anything(), 'soft');
  });

  it('saves and applies local viewpoints via getMap seam (M5)', () => {
    commitSpec({ scene: { mode: '3d' }, sources: {} });
    const easeTo = vi.fn();
    const getMap = () => ({
      getCenter: () => ({ lng: 104.06, lat: 30.67 }),
      getZoom: () => 15.5,
      getPitch: () => 60,
      getBearing: () => 20,
      easeTo,
    }) as never;
    render(<SceneController getMap={getMap} />);
    openPanel();
    fireEvent.click(screen.getByRole('button', { name: 'sceneController.saveViewpoint' }));
    fireEvent.click(screen.getByRole('button', { name: 'sceneController.applyViewpoint' }));
    expect(easeTo).toHaveBeenCalledTimes(1);
    expect(easeTo.mock.calls[0][0]).toMatchObject({
      center: [104.06, 30.67], zoom: 15.5, pitch: 60, bearing: 20,
    });
  });
});

// ── R2-4：fallback source 解析（committed 无 terrain 块但有 dem 源）────
describe('SceneController fallback terrain source (R2)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    commitUserScene.mockReset().mockResolvedValue({ outcome: 'committed' });
    setIs3D.mockClear();
  });
  afterEach(() => cleanup());

  it('slider without committed terrain resolves dem source id into payload', async () => {
    commitSpec({
      scene: { mode: '3d' },
      sources: { dem: { type: 'raster-dem' } },
    });
    render(<SceneController />);
    openPanel();
    const slider = screen.getByLabelText(/sceneController.exaggeration/, { selector: 'input' });
    expect((slider as HTMLInputElement).disabled).toBe(false);
    fireEvent.change(slider, { target: { value: '2' } });
    await vi.waitFor(() => expect(commitUserScene).toHaveBeenCalled());
    const payload = commitUserScene.mock.calls[0][0];
    expect(payload.terrain).toMatchObject({ source: 'dem', exaggeration: 2 });
  });
});
