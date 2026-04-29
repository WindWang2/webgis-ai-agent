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
      <div className="text-[14px] font-semibold text-slate-800 leading-tight">{title}</div>
      {sub && (
        <div className="text-[11px] text-slate-400 mt-0.5 leading-tight">{sub}</div>
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
      <label className="text-[10px] uppercase tracking-wide text-slate-400 font-medium">
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded bg-white/70 border border-slate-200/80 px-2 py-1 text-[12px] font-mono text-slate-700 placeholder:text-slate-300 focus:outline-none focus:ring-1 focus:ring-green-400/50 transition-shadow"
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
      className="inline-flex items-center justify-center gap-1.5 rounded px-3 py-1 text-[11px] font-medium text-white transition-all duration-150 hover:brightness-110 active:scale-[0.97]"
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
