import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import { CollapsibleThink } from './collapsible-think';

/* eslint-disable @typescript-eslint/no-require-imports */
vi.mock('framer-motion', () => {
  const fm = require('../../test/__mocks__/framer-motion');
  return {
    ...fm,
    AnimatePresence: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  };
});
/* eslint-enable @typescript-eslint/no-require-imports */

describe('CollapsibleThink Component', () => {
  it('renders null when content is empty and not active', () => {
    const { container } = render(<CollapsibleThink content="" />);
    expect(container.firstChild).toBeNull();
  });

  it('renders collapsed toggle button by default', () => {
    render(<CollapsibleThink content="这是推理过程..." />);

    const button = screen.getByRole('button', { name: /思考过程/i });
    expect(button).toBeInTheDocument();
    expect(button).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('这是推理过程...')).not.toBeInTheDocument();
  });

  it('expands and shows reasoning text when clicked', async () => {
    render(<CollapsibleThink content="这是详细推理内容" />);

    const button = screen.getByRole('button', { name: /思考过程/i });
    await act(async () => {
      fireEvent.click(button);
    });

    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('这是详细推理内容')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: '思考过程详情' })).toBeInTheDocument();
  });

  it('auto-expands when isStreaming is true', () => {
    render(<CollapsibleThink content="正在生成的思考步骤" isStreaming={true} />);

    const button = screen.getByRole('button', { name: /深度思考中/i });
    expect(button).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByText('正在生成的思考步骤')).toBeInTheDocument();
  });

  it('displays duration and token count badges when finished', () => {
    render(
      <CollapsibleThink
        content="思考完成"
        durationMs={1500}
        tokenCount={320}
      />
    );

    expect(screen.getByText('1.5s')).toBeInTheDocument();
    expect(screen.getByText('320 tokens')).toBeInTheDocument();
  });
});
