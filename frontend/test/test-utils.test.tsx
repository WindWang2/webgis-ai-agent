/**
 * renderWithStore 对 useHudStore.getState 的 spy 必须在用例结束后恢复。
 * 两个用例有顺序依赖：第一个建立 spy，第二个验证同文件后续用例看到真实
 * store（否则 setState 写入真实 store 却对 getState 不可见，形成假绿）。
 */
import { describe, expect, it } from 'vitest';
import { renderWithStore } from '@/test/test-utils';
import { useHudStore } from '@/lib/store/useHudStore';

describe('renderWithStore mock lifecycle', () => {
  it('renders against the mocked store state', () => {
    renderWithStore(<div data-testid="probe" />, { baseLayer: 'Mock 底图' });
    expect(useHudStore.getState().baseLayer).toBe('Mock 底图');
  });

  it('restores the real getState after the previous test', () => {
    useHudStore.setState({ baseLayer: 'ESRI 影像' });
    expect(useHudStore.getState().baseLayer).toBe('ESRI 影像');
  });

  it('mock state carries the real HudState focusLayerId key', () => {
    renderWithStore(<div />);
    expect(useHudStore.getState().focusLayerId).toBeNull();
  });
});
