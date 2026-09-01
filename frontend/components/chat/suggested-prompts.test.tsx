import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SuggestedPrompts } from './suggested-prompts';

/* eslint-disable @typescript-eslint/no-require-imports */
vi.mock('framer-motion', () => {
  const fm = require('../../test/__mocks__/framer-motion');
  return { motion: fm.motion, AnimatePresence: fm.AnimatePresence };
});

describe('SuggestedPrompts', () => {
  it('renders default suggestions with semantic tokens and handles clicks', () => {
    const onSend = vi.fn();
    render(<SuggestedPrompts onSend={onSend} />);

    const firstBtn = screen.getByText('分析北京市学校分布');
    expect(firstBtn).toBeInTheDocument();

    const buttonElement = firstBtn.closest('button');
    expect(buttonElement).toBeInTheDocument();
    expect(buttonElement?.className).toContain('bg-surface-sunken');
    expect(buttonElement?.className).toContain('border-edge-subtle');
    expect(buttonElement?.className).toContain('text-ink-secondary');

    fireEvent.click(firstBtn);
    expect(onSend).toHaveBeenCalledWith('分析北京市学校分布');
  });

  it('renders custom suggestions when provided', () => {
    const onSend = vi.fn();
    const custom = [
      { text: '查询上海市地铁线路' },
      { text: '生成高程分析' },
    ];
    render(<SuggestedPrompts onSend={onSend} suggestions={custom} />);

    expect(screen.getByText('查询上海市地铁线路')).toBeInTheDocument();
    expect(screen.getByText('生成高程分析')).toBeInTheDocument();
    expect(screen.queryByText('分析北京市学校分布')).not.toBeInTheDocument();
  });
});
