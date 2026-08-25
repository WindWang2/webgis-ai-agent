import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render } from '@testing-library/react';
import { EmbodiedHud } from './embodied-hud';

/**
 * #1001 — HUD 展开期间空闲态不得跑 60fps rAF setState 循环。旧实现一旦
 * 展开 rAF 无条件运行 setPhase（空闲步进 0.08），每帧重渲染 HUD 子树直到
 * 手动收起。空闲相位冻结为静态曲线；仅 isThinking 保留 JS 相位驱动。
 * prefers-reduced-motion 语义保留（两者都不启动）。
 */
const storeState = vi.hoisted(() => ({
  hudOpen: true,
  aiStatus: 'idle' as string,
  setHudOpen: vi.fn(),
  viewport: { center: [116.4074, 39.9074] as [number, number], zoom: 4, bearing: 0, pitch: 0 },
  baseLayer: 'Carto 深色',
  layers: [] as unknown[],
  theme: 'light' as string,
  setTheme: vi.fn(),
  is3D: false,
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector(storeState),
}));

vi.mock('@/lib/hooks/use-prefers-reduced-motion', () => ({
  usePrefersReducedMotion: () => false,
}));

describe('EmbodiedHud — idle rAF discipline (#1001)', () => {
  let rafSpy: ReturnType<typeof vi.spyOn>;

  beforeEach(() => {
    rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockReturnValue(0);
    vi.spyOn(window, 'cancelAnimationFrame').mockReturnValue();
    storeState.hudOpen = true;
    storeState.aiStatus = 'idle';
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('expanded + idle: does NOT schedule a single rAF (static waveform)', () => {
    render(<EmbodiedHud />);
    expect(rafSpy).not.toHaveBeenCalled();
  });

  it('expanded + thinking: keeps the JS phase loop (rAF scheduled)', () => {
    storeState.aiStatus = 'acting';
    render(<EmbodiedHud />);
    expect(rafSpy).toHaveBeenCalled();
  });

  it('collapsed: no rAF regardless of status (existing #692 semantics)', () => {
    storeState.hudOpen = false;
    storeState.aiStatus = 'acting';
    render(<EmbodiedHud />);
    expect(rafSpy).not.toHaveBeenCalled();
  });
});
