'use client';

import React, { memo, useState, useCallback, useMemo } from 'react';
import { Copy, Check, Terminal, Code2 } from 'lucide-react';
import { devOnly } from '@/lib/utils/logger';
import { tokenizeCode, getLanguageLabel, getTokenClassName } from './tokenizer';

export interface CodeBlockProps {
  /** 代码语言 */
  language?: string;
  /** 代码内容 */
  code: string;
  /** 是否显示行号 (可选，默认多行时自动显示) */
  showLineNumbers?: boolean;
  /** 文件名或标题 (可选) */
  filename?: string;
  /** 自定义外层样式 */
  className?: string;
}

/**
 * Modernized CodeBlock component with syntax highlighting, copy-to-clipboard,
 * theme-adaptive contrast, and smooth layout stability during streaming.
 */
export const CodeBlock = memo(function CodeBlock({
  language = '',
  code = '',
  showLineNumbers,
  filename,
  className = '',
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      devOnly.error('Failed to copy code:', err);
    }
  }, [code]);

  const langLabel = useMemo(() => getLanguageLabel(language), [language]);
  const tokenizedLines = useMemo(() => tokenizeCode(code, language), [code, language]);
  const lineCount = tokenizedLines.length;

  // Multi-line code >= 3 lines shows line numbers by default unless explicitly disabled
  const shouldShowLineNumbers = showLineNumbers !== undefined ? showLineNumbers : lineCount >= 3;

  const isShell = language === 'bash' || language === 'shell' || language === 'sh' || language === 'zsh';

  return (
    <div
      className={`my-2.5 rounded-md border border-edge-subtle bg-surface-sunken text-body shadow-sm overflow-hidden ${className}`}
      data-testid="code-block"
    >
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-surface-raised border-b border-edge-subtle text-caption select-none">
        <div className="flex items-center gap-1.5 text-ink-secondary font-medium">
          {isShell ? (
            <Terminal size={13} className="text-status-info shrink-0" aria-hidden />
          ) : (
            <Code2 size={13} className="text-status-accent shrink-0" aria-hidden />
          )}
          {filename && <span className="font-mono text-ink font-semibold">{filename}</span>}
          {language && (
            <span
              className="px-1.5 py-0.5 rounded text-micro font-mono font-medium bg-surface-sunken text-ink-secondary border border-edge-subtle"
              data-testid="language-pill"
            >
              {language}
            </span>
          )}
          {!language && !filename && (
            <span className="text-micro font-mono text-ink-muted">Code</span>
          )}
        </div>

        {/* Copy button */}
        <button
          type="button"
          onClick={handleCopy}
          className={`flex items-center gap-1 px-2 py-0.5 rounded text-caption font-medium transition-all cursor-pointer ${
            copied
              ? 'text-status-success bg-status-success-soft border border-status-success-border'
              : 'text-ink-muted hover:text-ink hover:bg-surface-hover border border-transparent'
          }`}
          aria-label={copied ? '已复制' : '复制代码'}
          title={copied ? '已复制到剪贴板' : '复制代码到剪贴板'}
        >
          {copied ? (
            <>
              <Check size={12} className="text-status-success" aria-hidden />
              <span>已复制</span>
            </>
          ) : (
            <>
              <Copy size={12} className="text-ink-muted" aria-hidden />
              <span>复制</span>
            </>
          )}
        </button>
      </div>

      {/* Code content */}
      <div className="p-3 overflow-x-auto text-body font-mono leading-relaxed max-w-full">
        <pre
          className="m-0 p-0 bg-transparent text-ink font-mono"
          aria-label={language ? `${langLabel || language} 代码块` : '代码块'}
        >
          <code>
            {tokenizedLines.map((lineTokens, lineIdx) => (
              <div key={lineIdx} className="flex min-w-full">
                {shouldShowLineNumbers && (
                  <span
                    className="select-none text-right text-ink-disabled pr-3 mr-3 border-r border-edge-subtle/60 text-caption font-mono min-w-[2rem] tabular-nums"
                    aria-hidden
                  >
                    {lineIdx + 1}
                  </span>
                )}
                <span className="flex-1 whitespace-pre">
                  {lineTokens.length === 0 || (lineTokens.length === 1 && lineTokens[0].value === '') ? (
                    '\n'
                  ) : (
                    lineTokens.map((tok, tokIdx) => (
                      <span key={tokIdx} className={getTokenClassName(tok.type)}>
                        {tok.value}
                      </span>
                    ))
                  )}
                </span>
              </div>
            ))}
          </code>
        </pre>
      </div>
    </div>
  );
});

CodeBlock.displayName = 'CodeBlock';

/**
 * 解析消息内容，提取代码块
 * 支持 ```language\ncode\n``` 格式
 */
export function parseMessageContent(content: string): React.ReactNode[] {
  const elements: React.ReactNode[] = [];

  // 正则匹配 ```lang\ncode\n```
  const codeBlockRegex = /```(\w*)\n([\s\S]*?)```/g;

  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = codeBlockRegex.exec(content)) !== null) {
    // 添加代码块之前的文本
    if (match.index > lastIndex) {
      const text = content.slice(lastIndex, match.index);
      if (text.trim()) {
        elements.push(
          <span key={`text-${lastIndex}`} className="whitespace-pre-wrap">
            {text}
          </span>,
        );
      }
    }

    const language = match[1] || '';
    const code = match[2].trim();

    elements.push(
      <CodeBlock key={`code-${match.index}`} language={language} code={code} />,
    );

    lastIndex = match.index + match[0].length;
  }

  // 添加剩余文本
  if (lastIndex < content.length) {
    const remaining = content.slice(lastIndex);
    if (remaining.trim()) {
      elements.push(
        <span key={`text-end`} className="whitespace-pre-wrap">
          {remaining}
        </span>,
      );
    }
  }

  return elements;
}

export default CodeBlock;