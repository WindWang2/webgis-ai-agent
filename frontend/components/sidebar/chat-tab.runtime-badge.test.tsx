import { describe, it, expect, vi } from 'vitest';
import { ChatTab } from './chat-tab';
import { renderWithStore } from '@/test/test-utils';

describe('ChatTab — agent runtime badge', () => {
  it('shows powered by Pi above the conversation', () => {
    const { getByTestId } = renderWithStore(
      <ChatTab
        messages={[]}
        aiStatus="idle"
        onSend={vi.fn()}
        agentRuntime="pi"
      />,
    );
    expect(getByTestId('agent-runtime-badge')).toHaveTextContent('powered by Pi');
  });

  it('shows powered by ChatEngine above the conversation', () => {
    const { getByTestId } = renderWithStore(
      <ChatTab
        messages={[]}
        aiStatus="idle"
        onSend={vi.fn()}
        agentRuntime="chatengine"
      />,
    );
    expect(getByTestId('agent-runtime-badge')).toHaveTextContent('powered by ChatEngine');
  });
});
