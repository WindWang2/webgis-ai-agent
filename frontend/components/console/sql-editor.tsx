'use client';

import React, { useMemo, useRef } from 'react';
import { tokenizeCode, getTokenClassName } from '@/components/code-highlight/tokenizer';

/**
 * 轻量 SQL 高亮编辑器（ADR-0147）——透明 textarea 叠在 tokenize 高亮层上。
 *
 * 不做完整语言服务（任务书边界）；复用 code-highlight/tokenizer 的 SQL
 * 词法（tokenizer.ts:84）。两侧 font/padding/line-height 必须逐像素一致，
 * 滚动经 onScroll 同步。
 */
export function SqlEditor({
  value,
  onChange,
  rows = 3,
  id,
  testId,
  invalid = false,
  placeholder,
}: {
  value: string;
  onChange: (next: string) => void;
  rows?: number;
  id?: string;
  testId?: string;
  invalid?: boolean;
  placeholder?: string;
}) {
  const taRef = useRef<HTMLTextAreaElement>(null);
  const preRef = useRef<HTMLPreElement>(null);
  const lines = useMemo(() => tokenizeCode(value, 'sql'), [value]);

  const syncScroll = () => {
    if (preRef.current && taRef.current) {
      preRef.current.scrollTop = taRef.current.scrollTop;
      preRef.current.scrollLeft = taRef.current.scrollLeft;
    }
  };

  const shellCls = `relative w-full overflow-hidden rounded-md border bg-surface-sunken font-mono text-body-sm leading-5 ${
    invalid ? 'border-status-critical' : 'border-edge-subtle focus-within:border-status-accent'
  }`;

  return (
    <div className={shellCls} data-testid={testId ? `${testId}-shell` : undefined}>
      <pre
        ref={preRef}
        aria-hidden="true"
        className="pointer-events-none m-0 max-h-[120px] overflow-hidden whitespace-pre-wrap break-words px-2.5 py-2"
      >
        {lines.map((tokens, i) => (
          <div key={i} className="min-h-5">
            {tokens.length === 0
              ? '\n'
              : tokens.map((tok, j) => (
                  <span key={j} className={getTokenClassName(tok.type)}>
                    {tok.value}
                  </span>
                ))}
          </div>
        ))}
      </pre>
      <textarea
        ref={taRef}
        id={id}
        rows={rows}
        value={value}
        spellCheck={false}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        onScroll={syncScroll}
        data-testid={testId}
        className="absolute inset-0 h-full w-full resize-none overflow-auto whitespace-pre-wrap break-words bg-transparent px-2.5 py-2 font-mono text-body-sm leading-5 text-transparent caret-ink outline-none placeholder:text-ink-muted"
        aria-label={id ? undefined : '过滤表达式'}
      />
    </div>
  );
}
