import { describe, it, expect, vi, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useKeyboardShortcut } from './use-keyboard-shortcut';

describe('useKeyboardShortcut Hook', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  // T005-022: 快捷键测试
  it('calls onSend when Ctrl+Enter pressed', () => {
    const onSend = vi.fn();
    
    renderHook(() => useKeyboardShortcut({
      onSend,
    }));

    // Simulate keydown
    const event = new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true });
    document.dispatchEvent(event);

    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it('calls onSend when Meta+Enter pressed (Mac)', () => {
    const onSend = vi.fn();
    
    renderHook(() => useKeyboardShortcut({
      onSend,
    }));

    const event = new KeyboardEvent('keydown', { key: 'Enter', metaKey: true });
    document.dispatchEvent(event);

    expect(onSend).toHaveBeenCalledTimes(1);
  });

  it('calls onClear when Escape pressed', () => {
    const onClear = vi.fn();
    
    renderHook(() => useKeyboardShortcut({
      onClear,
    }));

    const event = new KeyboardEvent('keydown', { key: 'Escape' });
    document.dispatchEvent(event);

    expect(onClear).toHaveBeenCalledTimes(1);
  });

  it('does not call handlers when disabled', () => {
    const onSend = vi.fn();
    const onClear = vi.fn();
    
    renderHook(() => useKeyboardShortcut({
      onSend,
      onClear,
      disabled: true,
    }));

    const enterEvent = new KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true });
    const escEvent = new KeyboardEvent('keydown', { key: 'Escape' });
    
    document.dispatchEvent(enterEvent);
    document.dispatchEvent(escEvent);

    expect(onSend).not.toHaveBeenCalled();
    expect(onClear).not.toHaveBeenCalled();
  });

  it('does not trigger send on plain Enter', () => {
    const onSend = vi.fn();
    
    renderHook(() => useKeyboardShortcut({
      onSend,
    }));

    const event = new KeyboardEvent('keydown', { key: 'Enter' });
    document.dispatchEvent(event);

    expect(onSend).not.toHaveBeenCalled();
  });

  it('calls onSave when Ctrl+S pressed', () => {
    const onSave = vi.fn();
    
    renderHook(() => useKeyboardShortcut({
      onSave,
    }));

    const event = new KeyboardEvent('keydown', { key: 's', ctrlKey: true });
    document.dispatchEvent(event);

    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it('prevents default when calling handler', () => {
    const onSend = vi.fn();
    const preventDefault = vi.fn();
    
    renderHook(() => useKeyboardShortcut({
      onSend,
    }));

    const event = new KeyboardEvent('keydown', { 
      key: 'Enter', 
      ctrlKey: true,
      preventDefault 
    });
    Object.defineProperty(event, 'preventDefault', { value: preventDefault });
    document.dispatchEvent(event);

    expect(preventDefault).toHaveBeenCalled();
  });
});