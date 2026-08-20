import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { ToolCallChain } from './tool-call-card';

const focusLayer = vi.fn();

vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: (selector: (s: any) => any) =>
    selector({
      focusLayer,
      layers: [
        {
          id: 'ref:new',
          name: 'new layer',
          type: 'vector',
          visible: true,
          legend_spec: { type: 'graduated', field: 'x', breaks: [0, 1], palette: 'a', palette_colors: ['#fff', '#000'] },
        },
        { id: 'ref:old', name: 'old layer', type: 'vector', visible: true },
      ],
    }),
}));

describe('ToolCall focus binding (#689 fix 3)', () => {
  beforeEach(() => focusLayer.mockClear());

  it('routes own-layer focus to the call-owned layer, not the latest legend layer', async () => {
    const call: any = {
      id: 'tc-1',
      tool: 'create_thematic_map',
      status: 'completed',
      layerId: 'ref:old',
      result: { legend_spec: { type: 'graduated', field: 'x', breaks: [0, 10], palette: 'a', palette_colors: ['#fff', '#000'] } },
    };
    render(<ToolCallChain calls={[call]} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /工具调用链|个工具调用完成/ }));
    });
    await act(async () => {
      fireEvent.click(screen.getByText(/专题地图/).closest('button')!);
    });
    const focusBtn = await screen.findByRole('button', { name: /高亮此图层/ });
    expect((focusBtn as HTMLButtonElement).disabled).toBe(false);
    await act(async () => {
      fireEvent.click(focusBtn);
    });
    expect(focusLayer).toHaveBeenCalledWith('ref:old');
  });

  it('disables focus button when the call has no bound layer', async () => {
    const call: any = {
      id: 'tc-2',
      tool: 'create_thematic_map',
      status: 'completed',
      result: { legend_spec: { type: 'graduated', field: 'x', breaks: [0, 10], palette: 'a', palette_colors: ['#fff', '#000'] } },
    };
    render(<ToolCallChain calls={[call]} />);
    await act(async () => {
      fireEvent.click(screen.getByRole('button', { name: /工具调用链|个工具调用完成/ }));
    });
    await act(async () => {
      fireEvent.click(screen.getByText(/专题地图/).closest('button')!);
    });
    const focusBtn = await screen.findByRole('button', { name: /高亮此图层/ });
    expect((focusBtn as HTMLButtonElement).disabled).toBe(true);
    expect(focusBtn.getAttribute('aria-disabled')).toBe('true');
  });
});
