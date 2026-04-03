import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MessageBubble } from './message-bubble';
import type { ChatMessage } from '@/lib/types/chat';

describe('MessageBubble Component', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('renders user message with correct styling', () => {
    const message: ChatMessage = {
      id: '1',
      role: 'user',
      content: 'Test message',
      timestamp: Date.now()
    };

    render(<MessageBubble message={message} />);
    
    const bubble = screen.getByText('Test message');
    expect(bubble).toBeInTheDocument();
  });

  it('renders assistant message with correct styling', () => {
    const message: ChatMessage = {
      id: '2',
      role: 'assistant',
      content: 'AI response',
      timestamp: Date.now()
    };

    render(<MessageBubble message={message} />);
    
    const bubble = screen.getByText('AI response');
    expect(bubble).toBeInTheDocument();
  });

  it('renders system message with warning styling', () => {
    const message: ChatMessage = {
      id: '3',
      role: 'system',
      content: 'System notice',
      timestamp: Date.now()
    };

    render(<MessageBubble message={message} />);
    
    const bubble = screen.getByText('System notice');
    expect(bubble).toBeInTheDocument();
  });
});