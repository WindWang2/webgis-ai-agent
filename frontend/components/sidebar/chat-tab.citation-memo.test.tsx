/**
 * D-F8 延伸：citation 解析按消息对象身份缓存 —— 流式 token 批次只替换
 * 流式消息对象，历史用户消息的 splitCitationBlocks 不得每批次重跑。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { act, waitFor } from '@testing-library/react';

const citationCalls = vi.hoisted(() => ({ texts: [] as string[] }));
vi.mock('@/components/chat/citation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/chat/citation')>();
  return {
    ...actual,
    splitCitationBlocks: (text: string) => {
      citationCalls.texts.push(text);
      return actual.splitCitationBlocks(text);
    },
  };
});

import { ChatTab } from './chat-tab';
import { renderWithStore } from '@/test/test-utils';

const USER_TEXT = [
  '分析北京学校分布',
  '[1] 来源：《北京教育统计年鉴》 分块 chunk-1（L2 0.12）',
  '海淀区学校密度最高。',
].join('\n');

describe('ChatTab citation memoization', () => {
  beforeEach(() => {
    citationCalls.texts = [];
  });

  it('流式批次不重复解析历史用户消息的 citation 块', async () => {
    const userMsg = { id: 'u1', role: 'user' as const, content: USER_TEXT, timestamp: new Date() };
    const priorMsg = { id: 'a1', role: 'assistant' as const, content: '已收到', timestamp: new Date() };
    let messages = [
      userMsg,
      priorMsg,
      { id: 'a2', role: 'assistant' as const, content: '', timestamp: new Date() },
    ];
    const tree = () => (
      <ChatTab messages={messages} aiStatus="thinking" onSend={() => {}} />
    );
    const { rerender } = renderWithStore(tree());
    await waitFor(() => expect(document.body.textContent).toContain('海淀区学校密度最高'));
    await act(async () => {});
    citationCalls.texts = [];

    for (let i = 1; i <= 10; i += 1) {
      messages = messages.map((m) =>
        m.id === 'a2' ? { ...m, content: `${m.content} token-${i}` } : m
      );
      rerender(tree());
    }
    expect(citationCalls.texts.filter((t) => t === USER_TEXT)).toHaveLength(0);
  });
});
