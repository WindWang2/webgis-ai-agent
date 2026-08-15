import { fireEvent } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';
import { ChatTab } from './chat-tab';
import { renderWithStore } from '@/test/test-utils';

/**
 * #391 — Enter during IME composition must NOT send the message.
 *
 * Chinese/Japanese/Korean input methods deliver the committing Enter as a
 * keydown with `isComposing === true` (Safari: keyCode 229). The composer
 * used to treat it as send-Enter and shipped the half-composed pinyin string
 * as a chat message — a mis-send on the core interaction for CJK users.
 */
describe('ChatTab — IME composition guard (#391)', () => {
  const baseProps = {
    messages: [],
    aiStatus: 'idle' as const,
  };

  function renderComposer(onSend: (text: string) => void) {
    return renderWithStore(<ChatTab {...baseProps} onSend={onSend} />);
  }

  function typeInto(textarea: HTMLElement, value: string) {
    fireEvent.change(textarea, { target: { value } });
  }

  it('does not send when Enter keydown has isComposing=true', () => {
    const onSend = vi.fn();
    const { getByRole } = renderComposer(onSend);
    const textarea = getByRole('textbox') as HTMLTextAreaElement;
    typeInto(textarea, '北京人口密度');

    fireEvent.keyDown(textarea, {
      key: 'Enter',
      shiftKey: false,
      isComposing: true,
    });
    expect(onSend).not.toHaveBeenCalled();
    expect(textarea.value).toBe('北京人口密度');
  });

  it('does not send when Enter keydown has keyCode 229 (Safari)', () => {
    const onSend = vi.fn();
    const { getByRole } = renderComposer(onSend);
    const textarea = getByRole('textbox') as HTMLTextAreaElement;
    typeInto(textarea, 'shanghai');

    fireEvent.keyDown(textarea, { key: 'Enter', keyCode: 229, shiftKey: false });
    expect(onSend).not.toHaveBeenCalled();
  });

  it('still sends on a plain (non-composing) Enter', () => {
    const onSend = vi.fn();
    const { getByRole } = renderComposer(onSend);
    const textarea = getByRole('textbox') as HTMLTextAreaElement;
    typeInto(textarea, 'hello');

    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
    expect(onSend).toHaveBeenCalledTimes(1);
    expect(onSend).toHaveBeenCalledWith('hello');
  });
});
