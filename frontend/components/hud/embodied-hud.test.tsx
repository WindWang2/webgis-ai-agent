import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { EmbodiedHud } from './embodied-hud';

// #607: 假数据面板回归锁 —— embodied-hud 不得再渲染零生产者 state 的展示位
// （opsLog / causalChain / ragResults 全仓零生产者，唯一 pushOpLog 调用点在
// 已删除的零挂载 hook use-map-control.ts）。

// 模拟 reduced-motion，让波形 rAF 循环直接早退（hudOpen=true 时不空转）。
vi.mock('@/lib/hooks/use-prefers-reduced-motion', () => ({
  usePrefersReducedMotion: () => true,
}));

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) => selector({
    hudOpen: true,
    setHudOpen: vi.fn(),
    viewport: { center: [116.4074, 39.9042], zoom: 4, bearing: 0, pitch: 0 },
    baseLayer: 'Carto 深色',
    layers: [],
    theme: 'light',
    setTheme: vi.fn(),
    aiStatus: 'idle',
    is3D: false,
  }),
}));

describe('EmbodiedHud (#607: no fake data panels)', () => {
  beforeEach(() => vi.clearAllMocks());

  it('still renders the real telemetry columns (perception / cognitive core)', () => {
    render(<EmbodiedHud />);
    expect(screen.getByText(/感知系统/)).toBeInTheDocument();
    expect(screen.getByText(/认知中枢/)).toBeInTheDocument();
    // 真实数据位仍然存在：SPATIAL REF 来自 produced layers
    expect(screen.getByText(/SPATIAL REF/)).toBeInTheDocument();
  });

  it('does NOT render the zero-producer action log, causal chain or RAG MEM', () => {
    const { container } = render(<EmbodiedHud />);
    expect(container.innerHTML).not.toContain('AWAITING SPATIAL AI COMMANDS');
    expect(container.innerHTML).not.toContain('ACTION LOG');
    expect(container.innerHTML).not.toContain('[CAUSAL]');
    expect(container.innerHTML).not.toContain('[OP]');
    expect(container.innerHTML).not.toContain('RAG MEM');
    expect(container.innerHTML).not.toContain('RUNNING:');
  });
});