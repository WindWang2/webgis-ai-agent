'use client';

/**
 * SearchField — 统一搜索输入（UI V3 shared primitive）。
 *
 * 收敛审计发现的 3 种发散实现（data-sources 300ms debounce / history 即席 /
 * template-v2 200ms）。受控值 + 可选 debounce；Escape 清空；带清除按钮。
 *
 * UI V4：高度/圆角/配色改用 token，与 SField 同一控件刻度。
 */
import { useEffect, useState } from 'react';
import { Search, X } from 'lucide-react';

export interface SearchFieldProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** aria-label（输入框无可见 label 时必填语义） */
  'aria-label': string;
  /** debounce 毫秒；0 = 立即触发 */
  debounceMs?: number;
}

export function SearchField({ value, onChange, placeholder, debounceMs = 0, 'aria-label': ariaLabel }: SearchFieldProps) {
  const [draft, setDraft] = useState(value);

  // 外部值变化（如清空）同步回 draft
  useEffect(() => setDraft(value), [value]);

  useEffect(() => {
    if (debounceMs <= 0) return;
    const t = setTimeout(() => {
      if (draft !== value) onChange(draft);
    }, debounceMs);
    return () => clearTimeout(t);
  }, [draft, debounceMs, onChange, value]);

  return (
    <div className="relative">
      <Search
        size={13}
        aria-hidden
        className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-ink-muted"
      />
      <input
        type="search"
        value={draft}
        aria-label={ariaLabel}
        placeholder={placeholder}
        onChange={(e) => {
          setDraft(e.target.value);
          if (debounceMs <= 0) onChange(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Escape' && draft) {
            setDraft('');
            onChange('');
            e.stopPropagation();
          }
        }}
        className="h-control-lg w-full rounded-sm border border-edge-subtle bg-surface-sunken pl-7 pr-7 text-body text-ink placeholder:text-ink-disabled focus:border-status-accent-border focus:outline-none"
      />
      {draft && (
        <button
          type="button"
          aria-label="清空搜索"
          onClick={() => {
            setDraft('');
            onChange('');
          }}
          className="absolute right-1.5 top-1/2 flex h-control-sm w-control-sm -translate-y-1/2 items-center justify-center rounded-sm text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
        >
          <X size={12} aria-hidden />
        </button>
      )}
    </div>
  );
}

export default SearchField;
