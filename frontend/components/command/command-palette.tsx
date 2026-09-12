'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { Search, CornerDownLeft, ArrowLeft } from 'lucide-react';
import {
  subscribeCommands,
  getCommandsSnapshot,
  getRecentCommandIds,
} from '@/lib/commands/registry';
import { buildSearchIndex, searchIndexed } from '@/lib/commands/fuzzy';
import { formatShortcut } from '@/lib/commands/shortcut';
import type { CommandDef } from '@/lib/commands/types';
import {
  runCommandById,
  useCommandPaletteStore,
  useCommandPaletteScrollLock,
} from '@/lib/hooks/use-command-palette';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';

/**
 * 命令面板（Ctrl+K）——APG combobox 模式（ADR-0147）。
 *
 * a11y 契约：input role=combobox + aria-activedescendant 指向 listbox 内
 * 活动项；选中项变化经 aria-live 播报；焦点圈闭/焦点归还走共用
 * useDialogFocus；Escape 由 useDialogFocus 统一关闭。
 */

const LISTBOX_ID = 'command-palette-listbox';

interface Row {
  command: CommandDef;
  /** 分组显示名（最近使用组为合成组）。 */
  group: string;
  indices?: number[];
}

function HighlightedTitle({ title, indices }: { title: string; indices?: number[] }) {
  if (!indices || indices.length === 0) return <>{title}</>;
  const set = new Set(indices);
  const parts: React.ReactNode[] = [];
  let buf = '';
  let key = 0;
  let mode = false;
  for (let i = 0; i < title.length; i++) {
    const hit = set.has(i);
    if (hit !== mode) {
      if (buf) {
        parts.push(
          mode ? (
            <mark key={key++} className="bg-transparent font-semibold text-status-accent">
              {buf}
            </mark>
          ) : (
            <span key={key++}>{buf}</span>
          ),
        );
        buf = '';
      }
      mode = hit;
    }
    buf += title[i];
  }
  if (buf) {
    parts.push(
      mode ? (
        <mark key={key++} className="bg-transparent font-semibold text-status-accent">
          {buf}
        </mark>
      ) : (
        <span key={key++}>{buf}</span>
      ),
    );
  }
  return <>{parts}</>;
}

export function CommandPalette(): React.ReactElement | null {
  const surface = useCommandPaletteStore((s) => s.surface);
  const close = useCommandPaletteStore((s) => s.close);
  const open = surface === 'palette';

  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  /** 参数模式：收集 paramSpec 输入。非 null 时 Enter 执行带参命令。 */
  const [paramCommand, setParamCommand] = useState<CommandDef | null>(null);
  const [paramText, setParamText] = useState('');
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  const snapshot = useSyncExternalStore(subscribeCommands, getCommandsSnapshot, getCommandsSnapshot);
  const commands = useMemo(() => snapshot.filter((c) => !c.when || c.when()), [snapshot]);
  const byId = useMemo(() => new Map(commands.map((c) => [c.id, c])), [commands]);

  const rows = useMemo<Row[]>(() => {
    if (query.trim()) {
      const index = buildSearchIndex(commands);
      const hits = searchIndexed(index, query);
      const rows: Row[] = [];
      for (const hit of hits) {
        const command = byId.get(hit.id);
        if (!command) continue;
        rows.push({ command, group: command.group, indices: hit.indices });
      }
      return rows;
    }
    const recents = getRecentCommandIds()
      .map((id) => byId.get(id))
      .filter((c): c is CommandDef => Boolean(c));
    const rest = commands.filter((c) => !recents.some((r) => r.id === c.id));
    return [
      ...recents.map((command) => ({ command, group: '最近使用' })),
      ...rest.map((command) => ({ command, group: command.group })),
    ];
  }, [query, commands, byId]);

  useCommandPaletteScrollLock(open);
  useDialogFocus({
    open,
    containerRef,
    onEscape: useCallback(() => {
      if (paramCommand) {
        setParamCommand(null);
        setParamText('');
        return;
      }
      close();
    }, [paramCommand, close]),
    initialFocusSelector: 'input',
  });

  // 打开时重置查询态
  useEffect(() => {
    if (open) {
      setQuery('');
      setParamCommand(null);
      setParamText('');
      setActiveIndex(0);
    }
  }, [open]);

  // rows 变化后夹紧活动项
  useEffect(() => {
    setActiveIndex((i) => Math.min(i, Math.max(0, rows.length - 1)));
  }, [rows.length]);

  // 活动项滚动跟随（jsdom 无 scrollIntoView，可选中调用）
  useEffect(() => {
    if (!open) return;
    const el = listRef.current?.querySelector<HTMLElement>(`[data-opt-index="${activeIndex}"]`);
    el?.scrollIntoView?.({ block: 'nearest' });
  }, [activeIndex, open]);

  const runRow = useCallback(
    async (row: Row, source: 'palette' | 'shortcut') => {
      if (row.command.paramSpec) {
        setParamCommand(row.command);
        setParamText('');
        inputRef.current?.focus();
        return;
      }
      close();
      await runCommandById(row.command.id, { source });
    },
    [close],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (paramCommand) {
        if (e.key === 'Enter') {
          e.preventDefault();
          const cmd = paramCommand;
          const text = paramText.trim();
          setParamCommand(null);
          setParamText('');
          close();
          void runCommandById(cmd.id, { source: 'palette' }, text || undefined);
        }
        return;
      }
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault();
          if (rows.length) setActiveIndex((i) => (i + 1) % rows.length);
          break;
        case 'ArrowUp':
          e.preventDefault();
          if (rows.length) setActiveIndex((i) => (i - 1 + rows.length) % rows.length);
          break;
        case 'Home':
          e.preventDefault();
          setActiveIndex(0);
          break;
        case 'End':
          e.preventDefault();
          setActiveIndex(Math.max(0, rows.length - 1));
          break;
        case 'Enter': {
          e.preventDefault();
          const row = rows[activeIndex];
          if (row) void runRow(row, 'palette');
          break;
        }
        default:
          break;
      }
    },
    [paramCommand, paramText, rows, activeIndex, runRow, close],
  );

  if (!open) return null;

  // 分组行渲染：保持顺序，相邻同组合并
  let lastGroup = '';
  let optIndex = -1;

  return (
    <div
      className="fixed inset-0 z-[90] flex items-start justify-center bg-black/40 pt-[12vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
      data-testid="command-palette-overlay"
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label="命令面板"
        data-testid="command-palette"
        className="w-full max-w-[560px] overflow-hidden rounded-lg border border-edge-subtle bg-surface-raised shadow-2xl"
      >
        {paramCommand ? (
          <div className="flex items-center gap-2 border-b border-edge-subtle px-3 pt-2.5 text-meta text-ink-secondary">
            <button
              type="button"
              className="inline-flex items-center gap-1 rounded-sm px-1 py-0.5 hover:bg-surface-hover"
              onClick={() => {
                setParamCommand(null);
                setParamText('');
                inputRef.current?.focus();
              }}
            >
              <ArrowLeft size={12} aria-hidden />
              返回
            </button>
            <span>{paramCommand.paramSpec?.prompt}</span>
          </div>
        ) : null}
        <div className="flex items-center gap-2 border-b border-edge-subtle px-3 py-2.5">
          <Search size={15} aria-hidden className="shrink-0 text-ink-muted" />
          <input
            ref={inputRef}
            role="combobox"
            aria-expanded="true"
            aria-controls={LISTBOX_ID}
            aria-activedescendant={activeIndex >= 0 && rows[activeIndex] ? optionId(activeIndex) : undefined}
            aria-autocomplete="list"
            aria-label={paramCommand ? '命令参数' : '搜索命令'}
            value={paramCommand ? paramText : query}
            placeholder={paramCommand?.paramSpec?.placeholder ?? '输入命令名或关键词…'}
            className="w-full bg-transparent text-body text-ink outline-none placeholder:text-ink-muted"
            onChange={(e) => {
              if (paramCommand) setParamText(e.target.value);
              else {
                setQuery(e.target.value);
                setActiveIndex(0);
              }
            }}
            onKeyDown={onKeyDown}
            data-testid="command-palette-input"
          />
          <kbd className="shrink-0 rounded-sm border border-edge-subtle px-1.5 py-0.5 text-caption text-ink-muted">
            Esc
          </kbd>
        </div>
        <ul
          ref={listRef}
          id={LISTBOX_ID}
          role="listbox"
          aria-label="命令列表"
          className="max-h-[46vh] overflow-y-auto py-1"
          data-testid="command-palette-list"
        >
          {rows.length === 0 ? (
            <li role="presentation" className="px-4 py-6 text-center text-body-sm text-ink-muted">
              没有匹配的命令
            </li>
          ) : (
            rows.map((row) => {
              optIndex += 1;
              const idx = optIndex;
              const showGroup = row.group !== lastGroup;
              lastGroup = row.group;
              const Icon = row.command.icon;
              return (
                <React.Fragment key={row.command.id}>
                  {showGroup ? (
                    <li
                      role="presentation"
                      className="px-3 pb-1 pt-2 text-caption font-medium uppercase tracking-wide text-ink-muted"
                    >
                      {row.group}
                    </li>
                  ) : null}
                  <li
                    id={optionId(idx)}
                    role="option"
                    aria-selected={idx === activeIndex}
                    data-opt-index={idx}
                    className={`mx-1 flex cursor-pointer items-center gap-2.5 rounded-md px-2.5 py-1.5 text-body-sm ${
                      idx === activeIndex ? 'bg-surface-hover text-ink' : 'text-ink-secondary'
                    }`}
                    onMouseMove={() => setActiveIndex(idx)}
                    onClick={() => void runRow(row, 'palette')}
                    data-testid="command-palette-option"
                  >
                    {Icon ? (
                      <Icon size={14} className="shrink-0 text-ink-muted" />
                    ) : (
                      <span className="h-[14px] w-[14px] shrink-0" aria-hidden />
                    )}
                    <span className="min-w-0 flex-1 truncate">
                      <HighlightedTitle title={row.command.title} indices={row.indices} />
                      {row.command.description ? (
                        <span className="ml-2 text-caption text-ink-muted">{row.command.description}</span>
                      ) : null}
                    </span>
                    {row.command.shortcut ? (
                      <kbd className="shrink-0 rounded-sm border border-edge-subtle px-1.5 py-0.5 font-mono text-caption text-ink-muted">
                        {formatShortcut(row.command.shortcut)}
                      </kbd>
                    ) : row.command.paramSpec ? (
                      <CornerDownLeft size={13} aria-hidden className="shrink-0 text-ink-muted" />
                    ) : null}
                  </li>
                </React.Fragment>
              );
            })
          )}
        </ul>
        <div
          aria-live="polite"
          role="status"
          className="sr-only"
          data-testid="command-palette-announcer"
        >
          {rows[activeIndex]
            ? `第 ${activeIndex + 1} 项，共 ${rows.length} 项：${rows[activeIndex].command.title}`
            : `共 ${rows.length} 项`}
        </div>
      </div>
    </div>
  );
}

function optionId(index: number): string {
  return `${LISTBOX_ID}-opt-${index}`;
}
