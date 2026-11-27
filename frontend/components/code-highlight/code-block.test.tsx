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
    expect(screen.getByText(/print/)).toBeInTheDocument();
  });

  it('renders code without language', () => {
    render(<CodeBlock code="some code" />);

    expect(screen.queryByText('python')).not.toBeInTheDocument();
    expect(screen.getByText('some code')).toBeInTheDocument();
  });

  it('copies code to clipboard', async () => {
    const writeTextMock = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    });

    render(<CodeBlock code="test code content" />);

    const copyBtn = screen.getByRole('button', { name: /复制/i });
    fireEvent.click(copyBtn);

    expect(writeTextMock).toHaveBeenCalledWith('test code content');

    // Check for copied feedback
    expect(await screen.findByText('已复制')).toBeInTheDocument();
  });

  it('handles copy failure gracefully', async () => {
    const writeTextMock = vi.fn().mockRejectedValue(new Error('Clipboard error'));
    Object.assign(navigator, {
      clipboard: {
        writeText: writeTextMock,
      },
    });

    render(<CodeBlock code="test" />);

    const copyBtn = screen.getByRole('button', { name: /复制/i });
    fireEvent.click(copyBtn);

    expect(writeTextMock).toHaveBeenCalled();
  });

  it('renders line numbers for multi-line code', () => {
    render(<CodeBlock code={"line a\nline b\nline c"} />);
    expect(screen.getByText('1')).toBeInTheDocument();
    expect(screen.getByText('2')).toBeInTheDocument();
    expect(screen.getByText('3')).toBeInTheDocument();
  });

  it('renders filename when provided in header', () => {
    render(<CodeBlock code="const a = 1;" language="typescript" filename="script.ts" />);
    expect(screen.getByText('script.ts')).toBeInTheDocument();
    expect(screen.getByText('typescript')).toBeInTheDocument();
  });
});

describe('parseMessageContent', () => {
  it('parses simple text without code blocks', () => {
    const result = parseMessageContent('Hello world');
    expect(result).toHaveLength(1);
    const { container } = render(<>{result}</>);
    expect(container).toHaveTextContent('Hello world');
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

  it('parses code block with hyphenated language name', () => {
    const content = '```objective-c\nint main() { return 0; }\n```';
    const result = parseMessageContent(content);

    const codeBlock = result[0] as any;
    expect(codeBlock?.props?.language).toBe('objective-c');
    expect(codeBlock?.props?.code).toBe('int main() { return 0; }');
  });

  it('parses code block with plus-suffixed language name', () => {
    const content = '```c++\nint x = 1;\n```';
    const result = parseMessageContent(content);

    const codeBlock = result[0] as any;
    expect(codeBlock?.props?.language).toBe('c++');
    expect(codeBlock?.props?.code).toBe('int x = 1;');
  });

  it('renders unclosed code fence (mid-stream) as a code block, not literal backticks', () => {
    const content = 'Streaming output:\n```python\nprint(1)';
    const result = parseMessageContent(content);

    const codeBlocks = result.filter((el: any) => el?.props?.code !== undefined);
    expect(codeBlocks.length).toBe(1);
    expect((codeBlocks[0] as any).props.language).toBe('python');
    expect((codeBlocks[0] as any).props.code).toBe('print(1)');

    const { container } = render(<>{result}</>);
    expect(container.textContent).not.toContain('```');
    expect(screen.getByTestId('code-block')).toBeInTheDocument();
  });

  it('still parses a closed fence followed by trailing text', () => {
    const content = '```sql\nSELECT 1;\n```\nDone.';
    const result = parseMessageContent(content);

    const codeBlocks = result.filter((el: any) => el?.props?.code !== undefined);
    expect(codeBlocks.length).toBe(1);
    const { container } = render(<>{result}</>);
    expect(container).toHaveTextContent('Done.');
    expect(container.textContent).not.toContain('```');
  });
});