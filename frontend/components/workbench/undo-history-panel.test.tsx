import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { UndoFlash, UndoHistoryPanel } from './undo-history-panel';
import { useUndoHistoryStore } from '@/lib/hooks/use-undo-history';
import {
  journalOnly,
  redo,
  recordCommand,
  resetUndoForTests,
  undo,
} from '@/lib/workbench/undo';
import { useHudStore } from '@/lib/store/useHudStore';

function makeRecord(label: string, layerIds?: string[]) {
  return {
    label,
    kind: 'style' as const,
    actor: 'user' as const,
    layerIds,
    undo: vi.fn(),
    redo: vi.fn(),
  };
}

beforeEach(() => {
  resetUndoForTests();
  useUndoHistoryStore.getState().closePanel();
  useHudStore.setState({ opsLog: [] });
});

describe('UndoHistoryPanel', () => {
  it('时间线：撤销栈/重做栈/操作日志三区渲染', () => {
    recordCommand(makeRecord('调整样式 A', ['L1']));
    recordCommand(makeRecord('调整样式 B', ['L2']));
    journalOnly({ type: 'remove', label: '删除图层 C', actor: 'agent' });
    render(<UndoHistoryPanel />);
    act(() => useUndoHistoryStore.getState().openPanel());

    expect(screen.getByRole('dialog', { name: '操作历史' })).toBeInTheDocument();
    expect(screen.getByTestId('history-undo-stack')).toHaveTextContent('调整样式 B');
    expect(screen.getByTestId('history-undo-stack')).toHaveTextContent('调整样式 A');
    expect(screen.getByTestId('history-opslog')).toHaveTextContent('删除图层 C');
    expect(screen.getByTestId('history-opslog')).toHaveTextContent('不可逆');
  });

  it('撤销/重做按钮走既有 API 并刷新栈视图', () => {
    const cmd = makeRecord('样式微调');
    recordCommand(cmd);
    render(<UndoHistoryPanel />);
    act(() => useUndoHistoryStore.getState().openPanel());
    fireEvent.click(screen.getByTestId('history-undo'));
    expect(cmd.undo).toHaveBeenCalledTimes(1);
    // undo 后进重做栈
    expect(screen.getByTestId('history-redo')).toHaveTextContent('重做（样式微调）');
    fireEvent.click(screen.getByTestId('history-redo'));
    expect(cmd.redo).toHaveBeenCalledTimes(1);
  });

  it('「回退到此处」：连续 undo 停在目标之前', () => {
    const c1 = makeRecord('第 1 步');
    const c2 = makeRecord('第 2 步');
    const c3 = makeRecord('第 3 步');
    recordCommand(c1);
    recordCommand(c2);
    recordCommand(c3);
    render(<UndoHistoryPanel />);
    act(() => useUndoHistoryStore.getState().openPanel());
    const rollbacks = screen.getAllByTestId('history-rollback');
    // 栈顶在列表首：三行分别对应 第3步(idx2)/第2步(idx1)/第1步(idx0)
    fireEvent.click(rollbacks[2]); // 回退到第 1 步之前 → undo 3 次？ idx0 → steps = 3-0-1 = 2 → 停在第1步之前
    expect(c3.undo).toHaveBeenCalledTimes(1);
    expect(c2.undo).toHaveBeenCalledTimes(1);
    expect(c1.undo).not.toHaveBeenCalled();
  });

  it('按图层视图：按 layerIds 过滤栈内命令', () => {
    recordCommand(makeRecord('显隐 L1', ['L1']));
    recordCommand(makeRecord('重排', ['L2']));
    recordCommand(makeRecord('样式 L1 再改', ['L1']));
    render(<UndoHistoryPanel />);
    act(() => useUndoHistoryStore.getState().openPanel());
    fireEvent.click(screen.getByRole('tab', { name: '按图层' }));
    fireEvent.change(screen.getByTestId('history-layer-select'), { target: { value: 'L1' } });
    const log = screen.getByTestId('history-layer-log');
    expect(log).toHaveTextContent('显隐 L1');
    expect(log).toHaveTextContent('样式 L1 再改');
    expect(log).not.toHaveTextContent('重排');
  });

  it('Escape 关闭（useDialogFocus）', () => {
    render(<UndoHistoryPanel />);
    act(() => useUndoHistoryStore.getState().openPanel());
    fireEvent.keyDown(document.body, { key: 'Escape' });
    expect(useUndoHistoryStore.getState().isOpen).toBe(false);
  });
});

describe('UndoFlash（Ctrl+Z 可见反馈）', () => {
  it('journal 记录 undo/redo 时经 toast 播报（挂载时不播旧头）', async () => {
    // 用真实 undo 路径产生 journal（pushOpLog 进 opsLog）
    recordCommand(makeRecord('会触发 undo 的操作'));
    render(<UndoFlash />);
    // 挂载时 opsLog 已有 recordCommand 的头 → 不播报旧头
    await waitFor(() => expect(useHudStore.getState().opsLog.length).toBeGreaterThan(0));
    const { useToastStore } = await import('@/components/ui/toast');
    const toastSpy = vi.fn();
    vi.spyOn(useToastStore.getState(), 'addToast').mockImplementation(toastSpy);
    act(() => {
      undo();
    });
    await waitFor(() => expect(toastSpy).toHaveBeenCalledWith('撤销：会触发 undo 的操作', 'info'));
    act(() => {
      redo();
    });
    await waitFor(() => expect(toastSpy).toHaveBeenCalledWith('重做：会触发 undo 的操作', 'info'));
  });
});
