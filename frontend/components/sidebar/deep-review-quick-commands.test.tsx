import { fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ChatTab } from './chat-tab';
import { renderWithStore } from '@/test/test-utils';
import zhSidebar from '@/messages/zh-CN/sidebar.json';

/**
 * FE-04 回归（623f786e）：快捷指令按钮曾把 i18n key（`prompts.poi`）当成
 * prompt 原样发送，agent 收到的用户消息是键名而非文案。
 */
describe('ChatTab 快捷指令发送译后文本（FE-04）', () => {
  it('点击每个快捷指令都发送 catalog 中的译文而非 key', () => {
    const onSend = vi.fn();
    renderWithStore(<ChatTab messages={[]} aiStatus="idle" onSend={onSend} />);

    const prompts = zhSidebar.chat.prompts;
    for (const label of [prompts.poi, prompts.buffer, prompts.heatmap, prompts.overlay]) {
      const target = Array.from(document.querySelectorAll('button')).find(
        (b) => b.textContent === label,
      );
      expect(target).toBeTruthy();
      fireEvent.click(target as HTMLButtonElement);
    }

    expect(onSend.mock.calls.map((call) => call[0])).toEqual([
      prompts.poi,
      prompts.buffer,
      prompts.heatmap,
      prompts.overlay,
    ]);
    for (const sent of onSend.mock.calls.map((call) => call[0])) {
      expect(sent).not.toContain('prompts.');
    }
  });
});
