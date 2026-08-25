import { fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ChatTab } from './chat-tab';
import { renderWithStore } from '@/test/test-utils';

/**
 * #988 — isBusy 期间发送键切换为『停止』形态：中止进行中的 Agent 回合
 * （此前无任何取消入口，误发指令只能干等或整页刷新）。
 * #1000 — 失败终态的 InlineNotice 增加『重试上一条』：一键重发最近一条
 * user 指令，免去重新手打整条命令。
 */
describe('ChatTab — stop button (#988) + retry entry (#1000)', () => {
  it('#988: isBusy renders the stop control instead of send; clicking invokes onCancel', () => {
    const onCancel = vi.fn();
    const onSend = vi.fn();
    const { getByRole, queryByRole } = renderWithStore(
      <ChatTab messages={[]} aiStatus="acting" onSend={onSend} onCancel={onCancel} />
    );

    expect(queryByRole('button', { name: '发送消息' })).toBeNull();
    const stop = getByRole('button', { name: '停止生成' }) as HTMLButtonElement;
    expect(stop).toBeEnabled();

    fireEvent.click(stop);
    expect(onCancel).toHaveBeenCalledTimes(1);
    expect(onSend).not.toHaveBeenCalled();
  });

  it('#988: idle keeps the plain send button (no stop control)', () => {
    const { getByRole, queryByRole } = renderWithStore(
      <ChatTab messages={[]} aiStatus="idle" onSend={vi.fn()} onCancel={vi.fn()} />
    );

    expect(queryByRole('button', { name: '停止生成' })).toBeNull();
    // 空输入仍禁用（发送语义不变）
    expect(getByRole('button', { name: '发送消息' })).toBeDisabled();
  });

  it('#1000: error notice offers retry that resends the LAST user message', () => {
    const onSend = vi.fn();
    const messages = [
      { id: '1', role: 'assistant' as const, content: '', timestamp: null },
      { id: '2', role: 'user' as const, content: '分析北京学校密度', timestamp: new Date() },
      { id: '3', role: 'user' as const, content: '', timestamp: new Date() },
      { id: '4', role: 'assistant' as const, content: '', timestamp: new Date(), isThinking: false },
    ];
    const { getByRole } = renderWithStore(
      <ChatTab messages={messages} aiStatus="error" onSend={onSend} />
    );

    const retry = getByRole('button', { name: '重试上一条指令' }) as HTMLButtonElement;
    expect(retry).toBeEnabled();
    fireEvent.click(retry);
    // 取最近一条非空 user 消息（空内容的那条不算）
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith('分析北京学校密度');
  });

  it('#1000: no retry button when the transcript has no non-empty user message', () => {
    const { queryByRole } = renderWithStore(
      <ChatTab
        messages={[{ id: '1', role: 'assistant' as const, content: '你好', timestamp: null }]}
        aiStatus="error"
        onSend={vi.fn()}
      />
    );
    expect(queryByRole('button', { name: '重试上一条指令' })).toBeNull();
  });

  it('#1000: idle notice hidden — retry control only exists in the error terminal', () => {
    const { queryByRole } = renderWithStore(
      <ChatTab
        messages={[{ id: '2', role: 'user' as const, content: '分析北京学校密度', timestamp: new Date() }]}
        aiStatus="idle"
        onSend={vi.fn()}
      />
    );
    expect(queryByRole('button', { name: '重试上一条指令' })).toBeNull();
  });
});
