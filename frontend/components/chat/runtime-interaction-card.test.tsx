/**
 * RuntimeInteractionCard（G29）行为契约：
 * - select：选项渲染/键盘选择/Enter 提交；白名单外的值不可能产生（radio）。
 * - editor：多行中文与换行原样保留；IME 组词 Enter 不提交。
 * - confirm：是/否/取消三路独立。
 * - 状态机：submitting/answered/终态全禁用；answered 显示「已送达等待继续」。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { RuntimeInteractionCard } from './runtime-interaction-card';
import type { RuntimeInteractionCardState } from '@/lib/types/runtime-interaction';

function base(overrides: Partial<RuntimeInteractionCardState> = {}): RuntimeInteractionCardState {
  return {
    interactionId: 'iax-test0001',
    sessionId: 'sess-1',
    turnId: 'turn-1',
    method: 'select',
    title: '选择数据版本',
    options: ['人口数据（版本日期 2020-05-01；范围 [100,30,104,32]）', '人口数据（版本日期 2024-05-01；范围 [110,30,114,32]）'],
    status: 'open',
    ...overrides,
  };
}

describe('RuntimeInteractionCard', () => {
  it('select：radio 选择 + Enter 提交（含中文长选项）', () => {
    const onSubmit = vi.fn();
    render(<RuntimeInteractionCard state={base()} onSubmit={({ answer }) => onSubmit(answer)} />);
    const first = screen.getByRole('radio', { name: /2020-05-01/ });
    fireEvent.click(first);
    fireEvent.keyDown(first, { key: 'Enter' });
    expect(onSubmit).toHaveBeenCalledWith({
      value: '人口数据（版本日期 2020-05-01；范围 [100,30,104,32]）',
    });
  });

  it('editor：多行中文原样保留；IME 组词 Enter 不提交', () => {
    const onSubmit = vi.fn();
    render(
      <RuntimeInteractionCard
        state={base({ method: 'editor', prefill: '2020年人口分布', options: undefined })}
        onSubmit={({ answer }) => onSubmit(answer)}
      />,
    );
    const ta = screen.getByRole('textbox') as HTMLTextAreaElement;
    expect(ta.value).toBe('2020年人口分布');
    fireEvent.change(ta, { target: { value: '标题：成都市人口分布\n来源：统计局2024' } });
    // 输入法组词中的 Enter —— 不提交。
    fireEvent.keyDown(ta, { key: 'Enter', nativeEvent: { isComposing: true } } as never);
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(ta, { key: 'Enter', nativeEvent: { isComposing: false } } as never);
    expect(ta.value).toContain('\n'); // 换行在 editor 中不应被吞
  });

  it('confirm：是/否分别提交 confirmed true/false；取消独立', () => {
    const onSubmit = vi.fn();
    const onCancel = vi.fn();
    render(
      <RuntimeInteractionCard
        state={base({ method: 'confirm', message: '采用该标题？', options: undefined })}
        onSubmit={({ interactionId, answer }) => onSubmit(interactionId, answer)}
        onCancel={({ interactionId }) => onCancel(interactionId)}
      />,
    );
    // submit 按钮 = 确认「是」。
    fireEvent.click(screen.getByRole('button', { name: /提交回答|Submit answer/ }));
    // 「否」按钮。
    fireEvent.click(screen.getByRole('button', { name: /^否$|^No$/ }));
    fireEvent.click(screen.getByRole('button', { name: /取消|Dismiss/ }));
    expect(onSubmit).toHaveBeenCalledWith('iax-test0001', { confirmed: true });
    expect(onSubmit).toHaveBeenCalledWith('iax-test0001', { confirmed: false });
    expect(onCancel).toHaveBeenCalledWith('iax-test0001');
  });

  it('状态机：answered 态全禁用并披露等待继续；终态不渲染提交', () => {
    const onSubmit = vi.fn();
    const { rerender } = render(
      <RuntimeInteractionCard state={base({ status: 'answered', answerDisplay: '人口数据（…2024…）' })} onSubmit={({ answer }) => onSubmit(answer)} />,
    );
    expect(screen.getByRole('radio', { name: /2020/ })).toBeDisabled();
    expect(screen.queryByRole('button', { name: /提交回答|Submit/ })).toBeNull();
    rerender(
      <RuntimeInteractionCard state={base({ status: 'timed_out' })} onSubmit={({ answer }) => onSubmit(answer)} />,
    );
    expect(screen.queryByRole('button', { name: /提交回答|Submit/ })).toBeNull();
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it('input：空输入不可提交', () => {
    const onSubmit = vi.fn();
    render(
      <RuntimeInteractionCard state={base({ method: 'input', options: undefined })} onSubmit={({ answer }) => onSubmit(answer)} />,
    );
    const btn = screen.getByRole('button', { name: /提交回答|Submit answer/ }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    const input = screen.getByRole('textbox');
    fireEvent.change(input, { target: { value: '自定义标题' } });
    expect(screen.getByRole('button', { name: /提交回答|Submit answer/ }).disabled).toBe(false);
  });
});
