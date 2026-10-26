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
    expect(screen.getByRole('radiogroup')).toBeTruthy();
    expect(screen.getByRole('dialog')).toBeTruthy();
  });
});
