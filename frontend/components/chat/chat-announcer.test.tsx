import { describe, it, expect } from 'vitest';
import { render, screen, act } from '@testing-library/react';
import { ChatAnnouncer } from './chat-announcer';

/**
 * #467: the announcer inferred completion from the thinking/acting → idle
 * STATUS TRANSITION. Switching sessions mid-stream forces aiStatus to idle
 * (use-workspace-session.selectSession), so the announcer fired a false
 * "回复已完成" and read whatever transcript was current at that moment — the
 * restored session's last message or the 已恢复历史会话 system line. The
 * announcement must be tied to the session the turn belongs to.
 */

const messagesA = [{ role: 'assistant' as const, content: '会话 A 的回答' }];

function announcer(props: {
  aiStatus: string;
  sessionId?: string | null;
  messages?: Array<{ role: 'user' | 'assistant'; content: string }>;
}) {
  return render(
    <ChatAnnouncer
      messages={props.messages ?? messagesA}
      aiStatus={props.aiStatus as any}
      sessionId={props.sessionId}
    />,
  );
}

function liveText(): string {
  return screen.getByRole('status').textContent ?? '';
}

describe('ChatAnnouncer — session-scoped completion (#467)', () => {
  it('announces completion for a normal thinking → idle turn in the same session', () => {
    const view = announcer({ aiStatus: 'thinking', sessionId: 'sess-a' });
    expect(liveText()).toBe('正在分析指令');

    view.rerender(
      <ChatAnnouncer messages={messagesA} aiStatus="idle" sessionId="sess-a" />,
    );
    expect(liveText()).toBe('回复已完成：会话 A 的回答');
  });

  it('mid-stream session switch announces NO completion (selectSession forces idle)', () => {
    const view = announcer({ aiStatus: 'thinking', sessionId: 'sess-a' });
    expect(liveText()).toBe('正在分析指令');

    // selectSession: aiStatus → idle + sessionId → B + the transcript becomes
    // the restored session's (here: the 已恢复历史会话 system line).
    view.rerender(
      <ChatAnnouncer
        messages={[{ role: 'assistant', content: '已恢复历史会话「B」——共 3 条记录。' }]}
        aiStatus="idle"
        sessionId="sess-b"
      />,
    );
    expect(liveText()).not.toContain('回复已完成');

    // And the restored transcript's content is never read as a completion.
    expect(liveText()).not.toContain('已恢复历史会话');
  });

  it('a later restore of messages in the SAME idle state stays silent', () => {
    const view = announcer({ aiStatus: 'thinking', sessionId: 'sess-a' });
    view.rerender(
      <ChatAnnouncer
        messages={[{ role: 'assistant', content: '已恢复历史会话「B」' }]}
        aiStatus="idle"
        sessionId="sess-b"
      />,
    );
    // The async session-content GET resolves after the switch — messages
    // change while status stays idle. Still no completion announcement.
    view.rerender(
      <ChatAnnouncer
        messages={[{ role: 'user', content: '历史用户消息' }, { role: 'assistant', content: '历史助手消息' }]}
        aiStatus="idle"
        sessionId="sess-b"
      />,
    );
    expect(liveText()).not.toContain('回复已完成');
  });

  it('a fresh turn in the NEW session still announces normally', () => {
    const view = announcer({ aiStatus: 'thinking', sessionId: 'sess-a' });
    view.rerender(
      <ChatAnnouncer messages={messagesA} aiStatus="idle" sessionId="sess-b" />,
    );
    expect(liveText()).not.toContain('回复已完成');

    // New session's own turn.
    view.rerender(
      <ChatAnnouncer
        messages={[{ role: 'assistant', content: '会话 B 的回答' }]}
        aiStatus="thinking"
        sessionId="sess-b"
      />,
    );
    expect(liveText()).toBe('正在分析指令');
    act(() => {});
    view.rerender(
      <ChatAnnouncer
        messages={[{ role: 'assistant', content: '会话 B 的回答' }]}
        aiStatus="idle"
        sessionId="sess-b"
      />,
    );
    expect(liveText()).toBe('回复已完成：会话 B 的回答');
  });

  it('new-session path (sessionId → undefined) is also suppressed', () => {
    const view = announcer({ aiStatus: 'acting', sessionId: 'sess-a' });
    expect(liveText()).toBe('正在执行空间操作');
    view.rerender(<ChatAnnouncer messages={messagesA} aiStatus="idle" />);
    expect(liveText()).not.toContain('回复已完成');
  });
});
