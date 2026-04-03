'use client';
import React, { memo, useState, useCallback } from 'react';
import { Copy, Check } from 'lucide-react';

interface CodeBlockProps {
  /** 代码语言 */
  language?: string;
  /** 代码内容 */
  code: string;
}

/**
 * 代码块组件 - 带语法高亮和复制功能
 * T005-017: 代码块高亮和复制功能
 */
export const CodeBlock = memo(function CodeBlock({
  language = '',
  code,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
  }, [code]);

  // 语言映射到CSS类名
  const langMap: Record<string, string> = {
    python: 'language-python',
    javascript: 'language-javascript',
    js: 'language-javascript',
    typescript: 'language-typescript',
    ts: 'language-typescript',
    json: 'language-json',
    bash: 'language-bash',
    shell: 'language-bash',
    sql: 'language-sql',
    html: 'language-html',
    css: 'language-css',
  };

  const langClass = langMap[language.toLowerCase()] || '';

  return (
    <div className="my-3 rounded-lg overflow-hidden bg-gray-900 text-gray-100">
      {/* Header */}
      <div className="flex items-center justify-between px-3 py-2 bg-gray-800">
        {language && (
          <span className="text-xs text-gray-400 font-mono">{language}</span>
        )}
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-xs text-gray-400 hover:text-white transition-colors"
          aria-label={copied ? '已复制' : '复制代码'}
        >
          {copied ? (
            <>
              <Check size={14} />
              <span>已复制</span>
            </>
          ) : (
            <>
              <Copy size={14} />
              <span>复制</span>
            </>
          )}
        </button>
      </div>

      {/* Code Content */}
      <pre className="p-3 overflow-x-auto text-sm font-mono leading-relaxed">
        <code className={langClass}>{code}</code>
      </pre>
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
          </span>
        );
      }
    }

    const language = match[1] || '';
    const code = match[2].trim();
    
    elements.push(
      <CodeBlock
        key={`code-${match.index}`}
        language={language}
        code={code}
      />
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
        </span>
      );
    }
  }

  return elements;
}

export default CodeBlock;