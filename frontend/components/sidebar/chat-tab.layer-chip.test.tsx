import { describe, it, expect, vi } from 'vitest';
import { ChatTab } from './chat-tab';
import { renderWithStore } from '@/test/test-utils';

/**
 * The layer-mounted chip sits in a min-w-0 flex column. Without
 * shrink-0 + nowrap, the "查看结果" control shrinks to one CJK glyph
 * and wraps onto two lines (查 / 看 / 结 / 果).
 */
describe('ChatTab — layer-mounted result chip', () => {
  it('keeps 查看结果 on a single line when the layer name is long', () => {
    const { getByRole } = renderWithStore(
      <ChatTab
        messages={[
          {
            id: 'a1',
            role: 'assistant',
            content: '已完成分布可视化',
            timestamp: new Date(),
            layerAdded: '分析结果: h3_binning',
            resultId: 'result-h3',
          },
        ]}
        aiStatus="idle"
        onSend={vi.fn()}
      />,
    );

    const button = getByRole('button', { name: '在结果工作台查看分析结果' });
    expect(button).toHaveTextContent('查看结果');
    expect(button.className).toMatch(/\bwhitespace-nowrap\b/);
    expect(button.className).toMatch(/\bshrink-0\b/);
  });
});
