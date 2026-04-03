import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { CodeBlock, parseMessageContent } from './code-block';

describe('CodeBlock Component', () => {
  // T005-016: 代码块高亮测试
  it('renders code with language label', () => {
    render(
      <CodeBlock language="python" code="print('hello')" />
    );

    expect(screen.getByText('python')).toBeInTheDocument();
    expect(screen.getByText(/print\('hello'\)/)).toBeInTheDocument();
  });

  it('renders code without language', () => {
    render(<CodeBlock code="some code" />);

    expect(screen.queryByText('python')).not.toBeInTheDocument();
    expect(screen.getByText('some code')).toBeInTheDocument();
  });

  it.skip('copies code to clipboard', async () => {
    vi.stubGlobal('navigator', {
      clipboard: {
        writeText: vi.fn().mockResolvedValue(undefined),
      },
    });

    render(<CodeBlock code="test code content" />);

    const copyBtn = screen.getByRole('button', { name: /复制/i });
    fireEvent.click(copyBtn);

    expect(navigator.clipboard.writeText).toHaveBeenCalledWith('test code content');

    // Check for copied feedback
    expect(await screen.findByText('已复制')).toBeInTheDocument();

    vi.unstubAllGlobals();
  });

  it.skip('handles copy failure gracefully', async () => {
    vi.stubGlobal('navigator', {
      clipboard: {
        writeText: vi.fn().mockRejectedValue(new Error('Clipboard error')),
      },
    });

    const consoleSpy = vi.spyOn(console, 'error').mockImplementation(() => {});

    render(<CodeBlock code="test" />);
    
    const copyBtn = screen.getByRole('button');
    fireEvent.click(copyBtn);

    expect(consoleSpy).toHaveBeenCalled();
    
    vi.unstubAllGlobals();
    consoleSpy.mockRestore();
  });
});

describe('parseMessageContent', () => {
  it.skip('parses simple text without code blocks', () => {
    const result = parseMessageContent('Hello world');
    expect(result).toHaveLength(1);
    expect(result[0]).toEqual(<span className="whitespace-pre-wrap">Hello world</span>);
  });

  it('parses code block with language', () => {
    const content = 'Try this code:\n```python\nprint("hi")\n```\nDone';
    const result = parseMessageContent(content);

    // Should have text + code block + text
    expect(result.length).toBeGreaterThanOrEqual(2);
  });

  it('parses multiple code blocks', () => {
    const content = '```python\ncode1\n```\nSome text\n```javascript\ncode2\n```';
    const result = parseMessageContent(content);

    // Find CodeBlock components
    const codeBlocks = result.filter(
      (el: any) => el?.props?.code
    );
    expect(codeBlocks.length).toBe(2);
  });

  it('handles code block without language', () => {
    const content = '```\nsome code\n```';
    const result = parseMessageContent(content);

    const codeBlock = result[0] as any;
    expect(codeBlock?.props?.code).toBe('some code');
  });
});