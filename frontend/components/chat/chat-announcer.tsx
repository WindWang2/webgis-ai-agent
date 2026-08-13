'use client';

import { useEffect, useRef, useState } from 'react';
import type { AiStatus } from '@/lib/store/hud-types';

/** Structural minimum: two message shapes exist in the app, both satisfy this. */
interface AnnounceableMessage {
  role: 'user' | 'assistant';
  content: string;
}

interface Props {
  messages: readonly AnnounceableMessage[];
  aiStatus: AiStatus;
}

/**
 * Screen-reader announcer for the chat turn lifecycle.
 *
 * The first attempt at this put `aria-live` on the message list itself. That is
 * wrong for this DOM shape: every token batch re-parses the streaming message's
 * markdown and *replaces* its whole subtree, so the smallest changed container
 * is the entire bubble and a screen reader re-reads the full accumulated answer
 * on every batch — dozens of times per turn. `aria-atomic="false"` only gives
 * you increments when the change is an append to a leaf node.
 *
 * So instead of narrating the stream, this announces the turn's *state* plus the
 * finished answer once. That is what a screen-reader user actually needs: know
 * that work started, know when it ended, then read the result at their own pace
 * with normal browse-mode navigation.
 */
export function ChatAnnouncer({ messages, aiStatus }: Props) {
  const [message, setMessage] = useState('');
  const lastStatus = useRef<AiStatus | null>(null);

  useEffect(() => {
    if (aiStatus === lastStatus.current) return;
    const previous = lastStatus.current;
    lastStatus.current = aiStatus;

    if (aiStatus === 'thinking') {
      setMessage('正在分析指令');
      return;
    }
    if (aiStatus === 'acting') {
      setMessage('正在执行空间操作');
      return;
    }
    if (aiStatus === 'error') {
      setMessage('指令执行失败，请调整后重试');
      return;
    }
    // Back to idle after work: announce the finished reply exactly once.
    if (previous === 'thinking' || previous === 'acting') {
      const last = messages[messages.length - 1];
      const text = last?.role === 'assistant' ? (last.content ?? '').trim() : '';
      setMessage(text ? `回复已完成：${text}` : '回复已完成');
    }
  }, [aiStatus, messages]);

  return (
    <div
      // sr-only: this duplicates content that is already on screen.
      className="sr-only"
      role="status"
      aria-live="polite"
      // atomic: the whole sentence is one announcement, not an increment.
      aria-atomic="true"
    >
      {message}
    </div>
  );
}

export default ChatAnnouncer;
