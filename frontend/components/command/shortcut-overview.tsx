'use client';

import React, { useMemo, useRef, useSyncExternalStore } from 'react';
import { AlertTriangle } from 'lucide-react';
import {
  detectShortcutConflicts,
  getCommandsSnapshot,
  subscribeCommands,
} from '@/lib/commands/registry';
import { formatShortcut } from '@/lib/commands/shortcut';
import { useCommandPaletteStore, useCommandPaletteScrollLock } from '@/lib/hooks/use-command-palette';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';

/**
 * 快捷键总览（`?` 唤起，ADR-0147）。
 *
 * 展示全注册表带快捷键的命令 + 冲突检测标注（同键多命令 → 警示行）。
 * 不带快捷键的命令不列（它们本就只从面板进入）。
 */
export function ShortcutOverview(): React.ReactElement | null {
  const surface = useCommandPaletteStore((s) => s.surface);
  const close = useCommandPaletteStore((s) => s.close);
  const open = surface === 'shortcuts';
  const containerRef = useRef<HTMLDivElement>(null);

  const snapshot = useSyncExternalStore(subscribeCommands, getCommandsSnapshot, getCommandsSnapshot);
  const visibleCommands = useMemo(() => snapshot.filter((c) => !c.when || c.when()), [snapshot]);
  const rows = useMemo(
    () =>
      visibleCommands
        .filter((c) => Boolean(c.shortcut))
        .map((c) => ({ id: c.id, title: c.title, group: c.group, shortcut: c.shortcut as string })),
    [visibleCommands],
  );
  // 冲突判定与列表同口径：只统计当前可见（when 通过）的命令——被隐藏的
  // 命令无法从面板触发，报其冲突只会误导用户。
  const conflicts = useMemo(() => detectShortcutConflicts(visibleCommands), [visibleCommands]);
  const titleOf = useMemo(() => new Map(visibleCommands.map((c) => [c.id, c.title])), [visibleCommands]);
  const conflictIds = useMemo(() => {
    const set = new Set<string>();
    conflicts.forEach((c) => c.commandIds.forEach((id) => set.add(id)));
    return set;
  }, [conflicts]);

  useCommandPaletteScrollLock(open);
  useDialogFocus({ open, containerRef, onEscape: close, initialFocusSelector: '[data-overview-close]' });

  if (!open) return null;

  const grouped = new Map<string, typeof rows>();
  for (const row of rows) {
    const list = grouped.get(row.group);
    if (list) list.push(row);
    else grouped.set(row.group, [row]);
  }

  return (
    <div
      className="fixed inset-0 z-[90] flex items-start justify-center bg-black/40 pt-[10vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
      data-testid="shortcut-overview-overlay"
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label="快捷键总览"
        data-testid="shortcut-overview"
        className="w-full max-w-[620px] overflow-hidden rounded-lg border border-edge-subtle bg-surface-raised shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-edge-subtle px-4 py-3">
          <h2 className="text-body-md font-semibold text-ink">快捷键总览</h2>
          <button
            type="button"
            data-overview-close
            onClick={close}
            className="rounded-sm px-2 py-1 text-body-sm text-ink-secondary hover:bg-surface-hover hover:text-ink"
          >
            关闭（Esc）
          </button>
        </div>
        {conflicts.length > 0 ? (
          <div
            role="alert"
            className="flex items-start gap-2 border-b border-edge-subtle bg-status-warning-soft px-4 py-2.5 text-body-sm text-ink"
            data-testid="shortcut-conflicts"
          >
            <AlertTriangle size={14} aria-hidden className="mt-0.5 shrink-0 text-status-warning" />
            <span>
              检测到 {conflicts.length} 处快捷键冲突：
              {conflicts
                .map(
                  (c) =>
                    `${formatShortcut(c.shortcut)}（${c.commandIds.map((id) => titleOf.get(id) ?? id).join(' / ')}）`,
                )
                .join('；')}
            </span>
          </div>
        ) : null}
        <div className="max-h-[60vh] overflow-y-auto px-4 py-2">
          {rows.length === 0 ? (
            <p className="py-6 text-center text-body-sm text-ink-muted">注册表中暂无快捷键</p>
          ) : (
            [...grouped.entries()].map(([group, list]) => (
              <section key={group} className="py-2" data-testid="shortcut-group">
                <h3 className="pb-1 text-caption font-medium uppercase tracking-wide text-ink-muted">
                  {group}
                </h3>
                <ul>
                  {list.map((row) => (
                    <li
                      key={row.id}
                      className={`flex items-center justify-between rounded-md px-2 py-1.5 text-body-sm ${
                        conflictIds.has(row.id) ? 'text-status-warning' : 'text-ink-secondary'
                      }`}
                      data-testid="shortcut-row"
                    >
                      <span className="min-w-0 truncate">{row.title}</span>
                      <kbd className="ml-3 shrink-0 rounded-sm border border-edge-subtle px-1.5 py-0.5 font-mono text-caption text-ink-muted">
                        {formatShortcut(row.shortcut)}
                      </kbd>
                    </li>
                  ))}
                </ul>
              </section>
            ))
          )}
          <p className="border-t border-edge-subtle py-2 text-caption text-ink-muted">
            地图工具栏另有单键快捷键（缩放/测量等，见地图工具栏提示）；此处列出命令注册表内声明项。
          </p>
        </div>
      </div>
    </div>
  );
}
