import { act } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ChatTab } from './chat-tab';
import { renderWithStore } from '@/test/test-utils';

function getScrollEl(container: HTMLElement): HTMLElement {
  // The scroll container is the only .overflow-y-auto inside ChatTab
  const el = container.querySelector('.overflow-y-auto') as HTMLElement;
  if (!el) throw new Error('scroll container not found');
  return el;
}

function setScrollMetrics(el: HTMLElement, { scrollHeight, clientHeight, scrollTop }: { scrollHeight: number; clientHeight: number; scrollTop: number }) {
  Object.defineProperty(el, 'scrollHeight', { value: scrollHeight, configurable: true });
  Object.defineProperty(el, 'clientHeight', { value: clientHeight, configurable: true });
  Object.defineProperty(el, 'scrollTop', { value: scrollTop, writable: true, configurable: true });
}

describe('ChatTab — streaming scroll hijack (#689 fix 1)', () => {
  const baseMessages = [
    { id: 'u1', role: 'user' as const, content: 'hello', timestamp: new Date() },
    { id: 'a1', role: 'assistant' as const, content: 'first chunk', timestamp: new Date() },
  ];

  it('does NOT auto-scroll when user has scrolled away from bottom (>80px)', async () => {
    const { container, rerender } = renderWithStore(
      <ChatTab messages={baseMessages} aiStatus="acting" onSend={() => {}} />,
    );
    const el = getScrollEl(container);
    // User is reading history: 700px away from bottom (far >80)
    setScrollMetrics(el, { scrollHeight: 1000, clientHeight: 300, scrollTop: 0 });
    // Spy on scrollTop setter by defining getter/setter tracking
    let scrolledTo: number | null = null;
    Object.defineProperty(el, 'scrollTop', {
      get() { return 0; },
      set(v: number) { scrolledTo = v; },
      configurable: true,
    });

    const nextMessages = [
      ...baseMessages,
      { id: 'a2', role: 'assistant' as const, content: 'streamed token batch', timestamp: new Date() },
    ];

    await act(async () => {
      rerender(<ChatTab messages={nextMessages as any} aiStatus="acting" onSend={() => {}} />);
      // flush effects
      await new Promise((r) => setTimeout(r, 0));
    });

    // Near-bottom guard should prevent scroll
    expect(scrolledTo).toBeNull();
  });

  it('auto-scrolls when already near bottom (<80px)', async () => {
    const { container, rerender } = renderWithStore(
      <ChatTab messages={baseMessages} aiStatus="acting" onSend={() => {}} />,
    );
    const el = getScrollEl(container);
    // Near bottom: 50px from bottom (<80), clientHeight 300, scrollHeight 1000 => scrollTop 650
    setScrollMetrics(el, { scrollHeight: 1000, clientHeight: 300, scrollTop: 650 });

    const nextMessages = [
      ...baseMessages,
      { id: 'a3', role: 'assistant' as const, content: 'next token', timestamp: new Date() },
    ];

    // Track setter
    let scrolledTo: number | null = null;
    // Need to redefine scrollTop as writable to capture assignment
    // and keep scrollHeight/clientHeight readable
    const height = 1000;
    Object.defineProperty(el, 'scrollHeight', { value: height, configurable: true });
    Object.defineProperty(el, 'clientHeight', { value: 300, configurable: true });
    // scrollTop currently 650; capture next write
    Object.defineProperty(el, 'scrollTop', {
      get() { return 650; },
      set(v: number) { scrolledTo = v; },
      configurable: true,
    });
    // Also need to allow reading scrollHeight during effect — getter above preserves height

    await act(async () => {
      rerender(<ChatTab messages={nextMessages as any} aiStatus="acting" onSend={() => {}} />);
      await new Promise((r) => setTimeout(r, 0));
    });

    // When near bottom, it should scroll to bottom (scrollHeight)
    expect(scrolledTo).not.toBeNull();
  });
});
