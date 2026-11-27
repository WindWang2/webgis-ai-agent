import { describe, it, expect, vi } from 'vitest';
import { screen, fireEvent } from '@testing-library/react';
import { AnalysisTab } from './analysis-tab';
import { renderWithStore } from '@/test/test-utils';

// renderWithStore 只 mock getState，不 mock selector hook；本文件需要真实
// layers 才能驱动图层下拉，故直接 mock useHudStore。
vi.mock('@/lib/store/useHudStore', () => {
  const layers = [
    { id: 'l1', name: 'roads', type: 'vector', visible: true },
    { id: 'l2', name: 'buildings', type: 'vector', visible: true },
  ];
  const state = { layers, setActiveLeftTab: () => {} };
  const hook = (selector: (s: typeof state) => unknown) => selector(state);
  return { useHudStore: Object.assign(hook, { getState: () => state }) };
});

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

  it('overlay / clip 选择同一图层时提交按钮禁用（不产生自叠加/自裁剪指令）', () => {
    const layers = mockLayers() as any;
    renderWithStore(<AnalysisTab onSend={vi.fn()} aiStatus="idle" />, { layers } as any);

    fireEvent.click(screen.getByRole('radio', { name: /叠加分析/ }));
    const overlaySelects = screen.getAllByRole('combobox');
    fireEvent.change(overlaySelects[0], { target: { value: 'l1' } });
    fireEvent.change(overlaySelects[1], { target: { value: 'l1' } });
    expect(screen.getByRole('button', { name: /生成叠加分析/ })).toBeDisabled();
    fireEvent.change(overlaySelects[1], { target: { value: 'l2' } });
    expect(screen.getByRole('button', { name: /生成叠加分析/ })).not.toBeDisabled();

    fireEvent.click(screen.getByRole('radio', { name: /裁剪/ }));
    const clipSelects = screen.getAllByRole('combobox');
    fireEvent.change(clipSelects[0], { target: { value: 'l1' } });
    fireEvent.change(clipSelects[1], { target: { value: 'l1' } });
    expect(screen.getByRole('button', { name: /执行裁剪/ })).toBeDisabled();
    fireEvent.change(clipSelects[1], { target: { value: 'l2' } });
    expect(screen.getByRole('button', { name: /执行裁剪/ })).not.toBeDisabled();
  });
});
