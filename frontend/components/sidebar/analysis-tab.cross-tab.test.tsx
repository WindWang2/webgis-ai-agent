import { describe, it, expect, vi } from 'vitest';
import { screen } from '@testing-library/react';
import { AnalysisTab } from './analysis-tab';
import { renderWithStore } from '@/test/test-utils';

function mockLayers() {
  return [
    { id: 'l1', name: 'roads', type: 'vector' as const, visible: true },
    { id: 'l2', name: 'buildings', type: 'vector' as const, visible: true },
  ];
}

describe('AnalysisTab — cross-tab submit + busy feedback (#689 fix 2)', () => {
  it('disables submit and shows busy text when aiStatus is thinking', () => {
    const layers = mockLayers() as any;
    renderWithStore(<AnalysisTab onSend={vi.fn()} aiStatus="thinking" />, {
      layers,
    } as any);

    expect(screen.getByRole('button', { name: /AI 忙碌中/ })).toBeInTheDocument();
    expect(screen.getByText(/AI 正在处理上一条指令/)).toBeInTheDocument();
    const btn = screen.getByRole('button', { name: /AI 忙碌中/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    expect(btn.getAttribute('aria-busy')).toBeTruthy();
  });

  it('disables submit and shows busy text when aiStatus is acting', () => {
    const layers = mockLayers() as any;
    renderWithStore(<AnalysisTab onSend={vi.fn()} aiStatus="acting" />, {
      layers,
    } as any);
    expect(screen.getByRole('button', { name: /AI 忙碌中/ })).toBeInTheDocument();
  });

  it('enables submit path when busy is false (no busy text)', () => {
    const layers = mockLayers() as any;
    renderWithStore(<AnalysisTab onSend={vi.fn()} aiStatus="idle" />, {
      layers,
    } as any);
    expect(screen.queryByText(/AI 忙碌中/)).not.toBeInTheDocument();
    expect(screen.queryByText(/AI 正在处理上一条指令/)).not.toBeInTheDocument();
  });

  it('defaults to idle behavior when aiStatus not provided', () => {
    const layers = mockLayers() as any;
    renderWithStore(<AnalysisTab onSend={vi.fn()} />, { layers } as any);
    expect(screen.queryByText(/AI 忙碌中/)).not.toBeInTheDocument();
  });
});
