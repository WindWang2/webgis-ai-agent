import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConfirmDialog } from './confirm-dialog';

// 焦点管理 hook 与渲染无关，测试直接 mock 掉。
vi.mock('@/lib/hooks/use-dialog-focus', () => ({
  useDialogFocus: vi.fn(),
}));

describe('ConfirmDialog (#553)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when closed', () => {
    const { container } = render(
      <ConfirmDialog open={false} title="T" onConfirm={vi.fn()} onCancel={vi.fn()} />
    );
    expect(container).toBeEmptyDOMElement();
  });

  it('confirm invokes onConfirm and not onCancel', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog
        open
        title="开始新对话？"
        description="开始新对话将清空当前工作区"
        confirmLabel="开始新对话"
        cancelLabel="取消"
        onConfirm={onConfirm}
        onCancel={onCancel}
      />
    );

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('开始新对话？')).toBeInTheDocument();
    expect(within(dialog).getByText('开始新对话将清空当前工作区')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: '开始新对话' }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
    expect(onCancel).not.toHaveBeenCalled();
  });

  it('cancel invokes onCancel and not onConfirm', async () => {
    const user = userEvent.setup();
    const onConfirm = vi.fn();
    const onCancel = vi.fn();
    render(
      <ConfirmDialog open title="T" onConfirm={onConfirm} onCancel={onCancel} />
    );

    await user.click(screen.getByRole('button', { name: '取消' }));
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onConfirm).not.toHaveBeenCalled();
  });
});
