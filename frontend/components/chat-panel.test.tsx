import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ChatPanel } from './chat-panel';

describe('ChatPanel Component', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  it('renders empty message list initially', () => {
    render(<ChatPanel messages={[]} onSendMessage={() => {}} />);
    
    const messageList = screen.getByRole('log');
    expect(messageList).toBeInTheDocument();
  });

  it('renders input field with placeholder', () => {
    render(<ChatPanel messages={[]} onSendMessage={() => {}} />);
    
    const input = screen.getByPlaceholderText(/输入你的地理问题/i);
    expect(input).toBeInTheDocument();
  });

  it('renders send button', () => {
    render(<ChatPanel messages={[]} onSendMessage={() => {}} />);
    
    const sendButton = screen.getByRole('button', { name: /send/i });
    expect(sendButton).toBeInTheDocument();
  });

  it('displays user and AI messages correctly', () => {
    const messages = [
      { id: '1', role: 'user', content: 'Hello AI', timestamp: Date.now() },
      { id: '2', role: 'assistant', content: 'Hello human', timestamp: Date.now() }
    ];
    
    render(<ChatPanel messages={messages} onSendMessage={() => {}} />);
    
    expect(screen.getByText('Hello AI')).toBeInTheDocument();
    expect(screen.getByText('Hello human')).toBeInTheDocument();
  });

  // T005-006: Input box functionality tests
  it('supports multi-line input', () => {
    const onSendMessage = vi.fn();
    render(<ChatPanel messages={[]} onSendMessage={onSendMessage} />);
    
    const textarea = screen.getByLabelText('Message input') as HTMLTextAreaElement;
    
    fireEvent.change(textarea, { target: { value: 'Line 1\nLine 2\nLine 3' } });
    expect(textarea.value).toBe('Line 1\nLine 2\nLine 3');
  });

  it('sends message on Enter key press', () => {
    const onSendMessage = vi.fn();
    render(<ChatPanel messages={[]} onSendMessage={onSendMessage} />);
    
    const textarea = screen.getByLabelText('Message input');
    fireEvent.change(textarea, { target: { value: 'Test message' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
    
    expect(onSendMessage).toHaveBeenCalledWith('Test message');
  });

  it('does not send on Shift+Enter', () => {
    const onSendMessage = vi.fn();
    render(<ChatPanel messages={[]} onSendMessage={onSendMessage} />);
    
    const textarea = screen.getByLabelText('Message input');
    fireEvent.change(textarea, { target: { value: 'Multi line' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: true });
    
    expect(onSendMessage).not.toHaveBeenCalled();
  });

  // T005-007: Clears input after send
  it('clears input after successful send', () => {
    const onSendMessage = vi.fn();
    render(<ChatPanel messages={[]} onSendMessage={onSendMessage} />);
    
    const textarea = screen.getByLabelText('Message input') as HTMLTextAreaElement;
    fireEvent.change(textarea, { target: { value: 'Test message' } });
    fireEvent.keyDown(textarea, { key: 'Enter', shiftKey: false });
    
    // Sync assertion - React state update is synchronous
    expect(onSendMessage).toHaveBeenCalledWith('Test message');
    expect(textarea.value).toBe('');
  });

  // T005-010: Loading state test
  it('shows loading indicator when isLoading is true', () => {
    render(<ChatPanel messages={[]} onSendMessage={() => {}} isLoading={true} />);
    
    const loader = screen.getByTestId('loading-indicator');
    expect(loader).toBeInTheDocument();
  });

  it('hides loading indicator when isLoading is false', () => {
    render(<ChatPanel messages={[]} onSendMessage={() => {}} isLoading={false} />);
    const loader = screen.queryByTestId('loading-indicator');
    expect(loader).not.toBeInTheDocument();
  });
});