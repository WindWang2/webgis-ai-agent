'use client';

import React from 'react';

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
      <div className="text-[14px] font-semibold text-[var(--theme-text-primary)] leading-tight">{title}</div>
      {sub && (
        <div className="text-[15px] text-[var(--theme-text-muted)] mt-0.5 leading-tight">{sub}</div>
      )}
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
}

export function SField({
  label,
  value,
  onChange,
  type = 'text',
  placeholder,
}: SFieldProps) {
  return (
    <div className="flex flex-col gap-1">
      <label className="text-[14px] uppercase tracking-wide text-[var(--theme-text-muted)] font-medium">
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded bg-[var(--theme-bg-input)] border border-[var(--theme-border)] px-2 py-1 text-[14px] font-mono text-[var(--theme-text-primary)] placeholder:text-[var(--theme-text-subtle)] focus:outline-none focus:ring-1 focus:ring-[color:var(--agent-accent)] transition-shadow"
      />
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  SButton — accent save / action button                              */
/* ------------------------------------------------------------------ */

interface SButtonProps {
  accentColor?: string;
  saved?: boolean;
  onClick: () => void;
  children?: React.ReactNode;
}

export function SButton({
  accentColor = '#16a34a',
  saved = false,
  onClick,
  children,
}: SButtonProps) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center justify-center gap-1.5 rounded px-3 py-1 text-[15px] font-medium text-white transition-all duration-150 hover:brightness-110 active:scale-[0.97]"
      style={{ backgroundColor: accentColor }}
    >
      {saved ? (
        <svg
          width="12"
          height="12"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <polyline points="3,8 6.5,11.5 13,4.5" />
        </svg>
      ) : (
        <svg
          width="12"
          height="12"
          viewBox="0 0 16 16"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <path d="M8 2v10M4 8l4 4 4-4" />
        </svg>
      )}
      {children ?? (saved ? 'Saved' : 'Save')}
    </button>
  );
}
