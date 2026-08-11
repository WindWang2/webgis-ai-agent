import { waitFor, act } from '@testing-library/react';
import { Profiler, type ProfilerOnRenderCallback } from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { ChatTab } from './chat-tab';
import { renderWithStore } from '@/test/test-utils';

/**
 * D-F8 — page-level `messages` state re-renders the whole app (P1).
 *
 * Every SSE token batch calls setMessages → ChatTab re-renders → the inline
 * `messages.map()` re-renders EVERY assistant message, and each one re-runs
 * react-markdown on its full content. Cost scales O(messages × batches): a
 * turn of N token batches over M prior messages re-parses the prior messages'
 * markdown N times each, even though their content never changed.
 *
 * This test simulates the real SSE update shape (use-sse-stream.ts replaces
 * ONLY the streaming message object per batch — prior message objects keep
 * their identity) and counts MiniMd invocations (= react-markdown parses).
 *
 * BEFORE (unmemoized): prior messages re-parse on every batch →
 *   markdown parses ≈ (M-1)×N + N. The assertions below FAIL.
 * AFTER (memoized per-message): only the streaming message re-parses →
 *   markdown parses == N, prior messages' text appears 0 times. PASS.
 */

// Realistic markdown body so parse work is representative (~300 chars,
// headers + list + table + code fence — exercises remark-gfm paths).
const PRIOR_MD = `## 北京市学校分布分析

**结论**：主城区学校密度较高，城六区平均每平方公里 12.3 所。

### 关键发现
1. 海淀区密度最高（18.7 所/km²）
2. 朝阳区总量最大（1,204 所）
3. 五环外密度明显下降

| 区 | 学校数 | 密度 |
|---|---|---|
| 海淀 | 312 | 18.7 |
| 朝阳 | 1,204 | 9.2 |

\`\`\`text
hotspot: 海淀区
\`\`\`
`;

// Count MiniMd invocations while still rendering the REAL MiniMd (real
// react-markdown parse), so both the count and the Profiler time are honest.
const md = vi.hoisted(() => ({ count: 0, texts: [] as string[] }));
vi.mock('@/components/chat/mini-md', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/components/chat/mini-md')>();
  return {
    ...actual,
    default: (props: { text: string }) => {
      md.count += 1;
      md.texts.push(props.text);
      return <div data-testid="mini-md">{actual.default(props)}</div>;
    },
  };
});

describe('ChatTab render scope (D-F8)', () => {
  beforeEach(() => {
    md.count = 0;
    md.texts = [];
  });

  it('streaming N token batches re-parses only the streaming message markdown', async () => {
    const N = 40;
    const userMsg = {
      id: 'u1',
      role: 'user' as const,
      content: '分析北京市学校分布',
      timestamp: new Date(),
    };
    const priorMsg = {
      id: 'a1',
      role: 'assistant' as const,
      content: PRIOR_MD,
      timestamp: new Date(),
    };
    const streamMsg = {
      id: 'a2',
      role: 'assistant' as const,
      content: '',
      timestamp: new Date(),
    };
    let messages: Array<{
      id: string;
      role: 'user' | 'assistant';
      content: string;
      timestamp: Date;
    }> = [userMsg, priorMsg, streamMsg];

    let profilerTotal = 0;
    const onRender: ProfilerOnRenderCallback = (_id, phase, actualDuration) => {
      if (phase === 'update') profilerTotal += actualDuration;
    };

    const tree = (
      <Profiler id="chat-render" onRender={onRender}>
        <ChatTab
          messages={messages}
          aiStatus="thinking"
          onSend={() => {}}
          accentColor="#16a34a"
        />
      </Profiler>
    );

    const { rerender } = renderWithStore(tree);

    // Settle: dynamic MiniMd resolves; `mounted` effect flips. Only priorMsg
    // has non-empty content at mount → exactly one markdown parse.
    await waitFor(() => expect(document.querySelectorAll('[data-testid="mini-md"]')).toHaveLength(1));
    await act(async () => {});
    md.count = 0;
    md.texts = [];
    profilerTotal = 0;

    // Simulate N SSE token batches: only the streaming message object is
    // replaced (same shape as use-sse-stream's setMessages updater).
    for (let i = 1; i <= N; i++) {
      messages = messages.map((m) =>
        m.id === 'a2' ? { ...m, content: `${m.content} token-${i}` } : m
      );
      rerender(
        <Profiler id="chat-render" onRender={onRender}>
          <ChatTab
            messages={messages}
            aiStatus="thinking"
            onSend={() => {}}
            accentColor="#16a34a"
          />
        </Profiler>
      );
    }

    const priorReParses = md.texts.filter((t) => t === PRIOR_MD).length;
    console.log(
      `[D-F8 ChatTab] N=${N} batches: markdown parses=${md.count} ` +
        `(stream-only bound=${N}), prior-msg re-parses=${priorReParses}, ` +
        `Profiler total=${profilerTotal.toFixed(2)}ms`
    );

    // The prior message's markdown must never be re-parsed while its content
    // is unchanged (BEFORE: N re-parses → FAIL; AFTER: 0 → PASS).
    expect(priorReParses).toBe(0);
    // Only the streaming message may parse per batch — at most N parses total
    // (BEFORE: ~2N → FAIL; AFTER: N → PASS).
    expect(md.count).toBeLessThanOrEqual(N);
    // The streaming message does parse once per batch (sanity floor).
    expect(md.count).toBeGreaterThanOrEqual(N);
  });
});
