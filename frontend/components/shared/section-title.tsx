'use client';

import React, { useId } from 'react';

/* ------------------------------------------------------------------ */
/*  STitle — section heading with optional subtitle                    */
/* ------------------------------------------------------------------ */

interface STitleProps {
  title: string;
  sub?: string;
}

export function STitle({ title, sub }: STitleProps) {
  return (
    <div className="mb-2">
      <div className="text-title font-semibold leading-tight text-ink">{title}</div>
      {/* 审计修复：sub 之前是 15px 而 title 是 14px —— 副标题比标题还大，
          层级是倒的。现在 sub 明确落在 title 下面一档。 */}
      {sub && <div className="mt-0.5 text-meta leading-tight text-ink-muted">{sub}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  SField — label + controlled input                                  */
/* ------------------------------------------------------------------ */

interface SFieldProps {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: 'text' | 'number' | 'password';
  placeholder?: string;
  /** 补充说明，渲染为 aria-describedby 关联的提示行 */
  hint?: string;
}

export function SField({
  label,
  value,
  onChange,
  type = 'text',
  placeholder,
  hint,
}: SFieldProps) {
  // a11y 修复（P0）：label 之前只是 input 的兄弟节点，既没有 htmlFor 也没有包裹，
  // 于是 LLM / RAG / 地图配置里全部 8 个设置项对辅助技术都是「无名输入框」。
  const inputId = useId();
  const hintId = `${inputId}-hint`;
  return (
    <div className="flex flex-col gap-1">
      <label htmlFor={inputId} className="eyebrow">
        {label}
      </label>
      <input
        id={inputId}
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        aria-describedby={hint ? hintId : undefined}
        className="h-control-lg w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2 font-mono text-body text-ink placeholder:text-ink-disabled focus:border-status-accent-border focus:outline-none"
      />
      {hint && (
        <p id={hintId} className="text-caption text-ink-muted">
          {hint}
        </p>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  SButton — accent save / action button                              */
/* ------------------------------------------------------------------ */

interface SButtonProps {
  saved?: boolean;
  onClick: () => void;
  children?: React.ReactNode;
}

export function SButton({ saved = false, onClick, children }: SButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      // 文字色用 --text-on-accent 而非白色：白字压在 accent 绿上只有 3.3:1，
      // 达不到正文 4.5:1。
      className="inline-flex h-control-lg items-center justify-center gap-1.5 rounded-sm bg-status-accent px-3 text-meta font-medium text-ink-on-accent transition-[filter] duration-150 hover:brightness-110 active:brightness-95"
    >
      <svg
        aria-hidden
        width="12"
        height="12"
        viewBox="0 0 16 16"
        fill="none"
        stroke="currentColor"
        strokeWidth={saved ? 2.5 : 2}
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {saved ? <polyline points="3,8 6.5,11.5 13,4.5" /> : <path d="M8 2v10M4 8l4 4 4-4" />}
      </svg>
      {children ?? (saved ? '已保存' : '保存')}
    </button>
  );
}
