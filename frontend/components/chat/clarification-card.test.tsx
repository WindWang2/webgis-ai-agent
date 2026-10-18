/**
 * ClarificationCard（ADR-0222）本地作答状态生命周期（#1628）。
 *
 * 服务端换入新 pending envelope（replaceClarificationEnvelope 置 open）时
 * 组件实例不变（chat-tab 按消息挂载，非按 request_id）—— 旧作答原样保留
 * 会让 radio 显示未选而 answerOf 仍返回旧值，用户「未重新选择即提交陈旧
 * 值」。契约：
 * - 换 envelope（新 request_id 或新 requirement_revision）→ 本地作答清空；
 * - 同 request_id 同 revision 的重取（服务端重放同一 pending）→ 在途作答保留。
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ClarificationCard } from './clarification-card';
import type {
  ClarificationCardState,
  ClarificationEnvelope,
} from '@/lib/types/clarification';

/** 单问题卡片：context_key 固定，选项集随 envelope 参数变化。 */
function makeEnvelope(
  opts: {
    requestId?: string;
    revision?: number;
    optionValues?: string[];
    canWaive?: boolean;
  } = {},
): ClarificationEnvelope {
  const values = opts.optionValues ?? ['500', '1000'];
  return {
    schema_version: 'clarification_envelope.v1',
    request_id: opts.requestId ?? 'req-1',
    session_id: 'sess-1',
    document_id: 'doc-1',
    requirement_revision: opts.revision ?? 1,
    raised_turn_id: 'turn-1',
    defaultable: true,
    questions: [
      {
        code: 'buffer_radius',
        context_key: 'params.buffer_radius',
        path: 'params.buffer_radius',
        question_zh: '缓冲区半径取多少？',
        question_en: 'Buffer radius?',
        options: values.map((v) => ({
          value: v,
          label_zh: `${v} 米`,
          label_en: `${v} m`,
          is_default: v === values[0],
        })),
        allow_free_text: false,
        can_waive: opts.canWaive ?? true,
        default_value: values[0],
        default_rationale: '安全默认',
        reason_summary: '',
      },
    ],
  };
}

function openState(env: ClarificationEnvelope): ClarificationCardState {
  return { envelope: env, status: 'open' };
}

/** 卡片唯一的按钮即提交按钮（open 态无其它按钮）。 */
function submitButton(): HTMLElement {
  return screen.getByRole('button', { name: /提交/ });
}

describe('ClarificationCard — 本地作答随 envelope 生命周期（#1628）', () => {
  it('换入新 envelope（同 context_key、选项集变化）→ 旧作答清空、提交禁用', () => {
    const { rerender } = render(
      <ClarificationCard state={openState(makeEnvelope())} onSubmit={vi.fn()} />,
    );

    // 在途作答：选中 1000 米。
    fireEvent.click(screen.getByLabelText('1000 米'));
    expect(screen.getByLabelText('1000 米')).toBeChecked();
    expect(submitButton()).toBeEnabled();

    // 服务端换入新 envelope：同 context_key 重新提问、选项集变化
    // （500/1000 → 1500/2000），requirement_revision 前进。
    rerender(
      <ClarificationCard
        state={openState(makeEnvelope({ revision: 2, optionValues: ['1500', '2000'] }))}
        onSubmit={vi.fn()}
      />,
    );

    // 新选项全部未选（旧值不在新选项集中，也不得残留为隐藏选中态）。
    for (const label of [/1500 米/, /2000 米/]) {
      expect(screen.getByRole('radio', { name: label })).not.toBeChecked();
    }
    // 无任何radio 处于选中态。
    expect(screen.getAllByRole('radio').some((r) => (r as HTMLInputElement).checked)).toBe(false);
    // 提交按钮禁用 —— 不得让用户「未重新选择即提交陈旧值」。
    expect(submitButton()).toBeDisabled();
  });

  it('新 request_id 的 envelope 同样清空本地作答', () => {
    const { rerender } = render(
      <ClarificationCard state={openState(makeEnvelope())} onSubmit={vi.fn()} />,
    );
    fireEvent.click(screen.getByLabelText('1000 米'));
    expect(submitButton()).toBeEnabled();

    rerender(
      <ClarificationCard
        state={openState(makeEnvelope({ requestId: 'req-2' }))}
        onSubmit={vi.fn()}
      />,
    );
    expect(screen.getByLabelText('1000 米')).not.toBeChecked();
    expect(submitButton()).toBeDisabled();
  });

  it('waived 残留随换 envelope 清除（重新提问不可 waive 时不得静默代答）', () => {
    const { rerender } = render(
      <ClarificationCard state={openState(makeEnvelope({ canWaive: true }))} onSubmit={vi.fn()} />,
    );
    // 勾「使用推荐默认值」→ answerOf 走 default_value → 可提交。
    fireEvent.click(screen.getByLabelText(/使用推荐默认值/));
    expect(submitButton()).toBeEnabled();

    // 新 envelope：同 context_key，但不再允许 waive（选项集亦变化）。
    rerender(
      <ClarificationCard
        state={openState(makeEnvelope({ revision: 2, optionValues: ['1500', '2000'], canWaive: false }))}
        onSubmit={vi.fn()}
      />,
    );
    // waive 复选框已消失； waived 残留被清除 → 提交禁用（不得按旧默认值代答）。
    expect(screen.queryByLabelText(/使用推荐默认值/)).toBeNull();
    expect(submitButton()).toBeDisabled();
  });

  it('同 request_id 同 revision 重取（服务端重放同一 pending）→ 在途作答保留', () => {
    const { rerender } = render(
      <ClarificationCard state={openState(makeEnvelope())} onSubmit={vi.fn()} />,
    );
    fireEvent.click(screen.getByLabelText('1000 米'));
    expect(screen.getByLabelText('1000 米')).toBeChecked();
    expect(submitButton()).toBeEnabled();

    // replaceClarificationEnvelope 的同 request_id 分支：新 envelope 对象，
    // 但 request_id 与 requirement_revision 均不变（内容相同的重放）。
    rerender(
      <ClarificationCard state={openState(makeEnvelope())} onSubmit={vi.fn()} />,
    );

    expect(screen.getByLabelText('1000 米')).toBeChecked();
    expect(submitButton()).toBeEnabled();
  });

  it('提交载荷携带用户在途选择（重置修复不破坏正常提交通道）', () => {
    const onSubmit = vi.fn();
    render(<ClarificationCard state={openState(makeEnvelope())} onSubmit={onSubmit} />);
    fireEvent.click(screen.getByLabelText('1000 米'));
    fireEvent.click(submitButton());
    expect(onSubmit).toHaveBeenCalledWith({
      requestId: 'req-1',
      answers: [{ context_key: 'params.buffer_radius', value: '1000' }],
      waiveKeys: [],
    });
  });
});
