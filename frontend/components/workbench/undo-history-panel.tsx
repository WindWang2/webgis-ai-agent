'use client';

import React, { useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { History, Undo2, Redo2, RotateCcw, X, MapPin } from 'lucide-react';
import {
  getRedoStack,
  getUndoStack,
  redo,
  subscribeUndo,
  undo,
  getUndoSnapshot,
} from '@/lib/workbench/undo';
import { useHudStore } from '@/lib/store/useHudStore';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { useToastStore } from '@/components/ui/toast';
import { useUndoHistoryStore } from '@/lib/hooks/use-undo-history';

/**
 * 操作历史弹层（ADR-0147 P5）。
 *
 * 三个数据源（均只读消费，不改 undo.ts 语义）：
 * - 撤销/重做栈：getUndoStack/getRedoStack（只增只读访问器）；
 * - 操作日志：useHudStore.opsLog（journal 全量留痕，含不可逆与撤销/重做标记）；
 * - 图层操作日志：栈内命令按 layerIds 过滤（样式/显隐/重排等带 layerIds 的命令）。
 *
 * 「回退到此处」= 连续执行 undo() 至目标深度（走既有 undo API，逐步反演）。
 */

const KIND_LABEL: Record<string, string> = {
  add: '新增',
  remove: '删除',
  toggle: '显隐/样式',
  flyto: '视角',
  style: '样式',
  sketch: '草图',
  undo: '撤销',
  redo: '重做',
  lock: '锁定',
  group: '分组',
  reorder: '重排',
  lock_conflict: '锁冲突',
};

function useUndoVersion(): number {
  return useSyncExternalStore(subscribeUndo, getUndoSnapshot, getUndoSnapshot);
}

type Tab = 'timeline' | 'layers';

export function UndoHistoryPanel(): React.ReactElement | null {
  const isOpen = useUndoHistoryStore((s) => s.isOpen);
  const closePanel = useUndoHistoryStore((s) => s.closePanel);
  useUndoVersion(); // 栈变化驱动重渲染

  const undoStack = getUndoStack();
  const redoStack = getRedoStack();
  const opsLog = useHudStore((s) => s.opsLog);
  const layers = useHudStore((s) => s.layers);

  const [tab, setTab] = useState<Tab>('timeline');
  const [selectedLayerId, setSelectedLayerId] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useDialogFocus({ open: isOpen, containerRef, onEscape: closePanel, initialFocusSelector: '[data-history-focus]' });

  // 图层视图：当前图层 ∪ 栈内出现过的 layerIds（历史图层仍可查）
  const layerOptions = useMemo(() => {
    const ids = new Map<string, string>();
    for (const l of layers) ids.set(l.id, l.name);
    for (const cmd of undoStack) {
      for (const id of cmd.layerIds ?? []) {
        if (!ids.has(id)) ids.set(id, id);
      }
    }
    return [...ids.entries()].map(([id, name]) => ({ id, name }));
  }, [layers, undoStack]);

  const layerCommands = useMemo(() => {
    if (!selectedLayerId) return [];
    return [...undoStack, ...redoStack]
      .filter((cmd) => (cmd.layerIds ?? []).includes(selectedLayerId))
      .reverse();
  }, [undoStack, redoStack, selectedLayerId]);

  if (!isOpen) return null;

  /** 回退到指定深度：连续 undo 直到目标成为栈顶之后（走既有 undo API）。 */
  const rollbackTo = (index: number) => {
    const target = undoStack[index];
    if (!target) return;
    const steps = undoStack.length - index - 1;
    if (steps <= 0) {
      useToastStore.getState().addToast('该操作已是最新状态，无需回退', 'info');
      return;
    }
    let done = 0;
    for (let i = 0; i < steps; i++) {
      if (undo()) done += 1;
    }
    useToastStore
      .getState()
      .addToast(done > 0 ? `已回退 ${done} 步，停在「${target.label}」之前` : '回退失败', done > 0 ? 'success' : 'error');
  };

  return (
    <div
      className="fixed inset-0 z-[80] flex items-start justify-center bg-black/40 p-4 pt-[8vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) closePanel();
      }}
      data-testid="undo-history-overlay"
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label="操作历史"
        data-testid="undo-history"
        className="flex max-h-[78vh] w-full max-w-[640px] flex-col overflow-hidden rounded-lg border border-edge-subtle bg-surface-raised shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-edge-subtle px-4 py-2.5">
          <h2 className="flex items-center gap-2 text-body-md font-semibold text-ink">
            <History size={15} aria-hidden />
            操作历史
          </h2>
          <button
            type="button"
            data-history-focus
            aria-label="关闭操作历史"
            onClick={closePanel}
            className="rounded-sm p-1 text-ink-secondary hover:bg-surface-hover hover:text-ink"
          >
            <X size={15} aria-hidden />
          </button>
        </div>

        {/* 全局撤销/重做 */}
        <div className="flex items-center gap-2 border-b border-edge-subtle px-4 py-2">
          <button
            type="button"
            onClick={() => {
              const next = undoStack.at(-1);
              if (undo()) useToastStore.getState().addToast(`已撤销：${next?.label ?? ''}`, 'success');
            }}
            disabled={undoStack.length === 0}
            className="inline-flex items-center gap-1 rounded-md border border-edge-subtle px-2.5 py-1 text-body-sm text-ink-secondary hover:bg-surface-hover hover:text-ink disabled:opacity-50"
            data-testid="history-undo"
          >
            <Undo2 size={13} aria-hidden />
            撤销{undoStack.length ? `（${undoStack.at(-1)?.label}）` : ''}
          </button>
          <button
            type="button"
            onClick={() => {
              const next = redoStack.at(-1);
              if (redo()) useToastStore.getState().addToast(`已重做：${next?.label ?? ''}`, 'success');
            }}
            disabled={redoStack.length === 0}
            className="inline-flex items-center gap-1 rounded-md border border-edge-subtle px-2.5 py-1 text-body-sm text-ink-secondary hover:bg-surface-hover hover:text-ink disabled:opacity-50"
            data-testid="history-redo"
          >
            <Redo2 size={13} aria-hidden />
            重做{redoStack.length ? `（${redoStack.at(-1)?.label}）` : ''}
          </button>
          <div className="ml-auto flex gap-1" role="tablist" aria-label="历史视图">
            {(['timeline', 'layers'] as const).map((t) => (
              <button
                key={t}
                type="button"
                role="tab"
                aria-selected={tab === t}
                onClick={() => setTab(t)}
                className={`rounded-sm px-2 py-1 text-caption font-medium ${
                  tab === t ? 'bg-surface-hover text-ink' : 'text-ink-muted hover:text-ink-secondary'
                }`}
              >
                {t === 'timeline' ? '时间线' : '按图层'}
              </button>
            ))}
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-3 py-2">
          {tab === 'timeline' ? (
            <>
              {/* 撤销栈（最新在上，可回退） */}
              <h3 className="px-1 pb-1 text-caption font-medium uppercase tracking-wide text-ink-muted">
                撤销栈（{undoStack.length}）
              </h3>
              {undoStack.length === 0 ? (
                <p className="px-1 py-1 text-caption text-ink-muted">空</p>
              ) : (
                <ul className="mb-3 space-y-1" data-testid="history-undo-stack">
                  {[...undoStack].reverse().map((cmd) => {
                    const idx = undoStack.indexOf(cmd);
                    return (
                      <li
                        key={cmd.id}
                        className="flex items-center gap-2 rounded-md px-2 py-1.5 text-body-sm text-ink-secondary hover:bg-surface-hover"
                      >
                        <span className="rounded-sm bg-surface-sunken px-1.5 py-0.5 text-caption text-ink-muted">
                          {KIND_LABEL[cmd.kind] ?? cmd.kind}
                        </span>
                        <span className="min-w-0 flex-1 truncate">
                          {cmd.label}
                          <span className="ml-2 text-caption text-ink-muted">
                            {cmd.actor === 'agent' ? '· agent' : ''} {new Date(cmd.ts).toLocaleTimeString('zh-CN')}
                          </span>
                        </span>
                        <button
                          type="button"
                          onClick={() => rollbackTo(idx)}
                          title={`连续回退到「${cmd.label}」之前`}
                          className="inline-flex shrink-0 items-center gap-1 rounded-sm border border-edge-subtle px-1.5 py-0.5 text-caption text-ink-muted hover:bg-surface-hover hover:text-ink"
                          data-testid="history-rollback"
                        >
                          <RotateCcw size={11} aria-hidden />
                          回退到此处
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}

              {/* 重做栈 */}
              <h3 className="px-1 pb-1 text-caption font-medium uppercase tracking-wide text-ink-muted">
                重做栈（{redoStack.length}）
              </h3>
              {redoStack.length === 0 ? (
                <p className="px-1 pb-2 text-caption text-ink-muted">空</p>
              ) : (
                <ul className="mb-3 space-y-1">
                  {[...redoStack].reverse().map((cmd) => (
                    <li key={cmd.id} className="truncate rounded-md px-2 py-1 text-body-sm text-ink-muted">
                      {cmd.label}
                    </li>
                  ))}
                </ul>
              )}

              {/* 操作日志（journal 全量，含不可逆） */}
              <h3 className="px-1 pb-1 text-caption font-medium uppercase tracking-wide text-ink-muted">
                操作日志（journal，最近 {Math.min(opsLog.length, 30)} 条）
              </h3>
              {opsLog.length === 0 ? (
                <p className="px-1 pb-2 text-caption text-ink-muted">空</p>
              ) : (
                <ul data-testid="history-opslog">
                  {opsLog.slice(0, 30).map((entry) => (
                    <li key={entry.id} className="flex items-center gap-2 px-2 py-1 text-caption text-ink-muted">
                      <span className="w-16 shrink-0 truncate font-mono">{entry.time}</span>
                      <span className="rounded-sm bg-surface-sunken px-1.5 py-0.5">{KIND_LABEL[entry.type] ?? entry.type}</span>
                      <span className="min-w-0 flex-1 truncate">{entry.label}</span>
                      {entry.reversible === false ? (
                        <span className="shrink-0 text-status-warning" title="不可逆操作，不在撤销栈内">
                          不可逆
                        </span>
                      ) : null}
                    </li>
                  ))}
                </ul>
              )}
            </>
          ) : (
            <>
              <div className="pb-2">
                <label htmlFor="history-layer-select" className="block pb-1 text-caption text-ink-muted">
                  选择图层
                </label>
                <select
                  id="history-layer-select"
                  value={selectedLayerId ?? ''}
                  onChange={(e) => setSelectedLayerId(e.target.value || null)}
                  className="w-full rounded-md border border-edge-subtle bg-surface-sunken px-2 py-1.5 text-body-sm text-ink"
                  data-testid="history-layer-select"
                >
                  <option value="">— 选择图层 —</option>
                  {layerOptions.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name}
                    </option>
                  ))}
                </select>
              </div>
              {!selectedLayerId ? (
                <p className="px-1 text-caption text-ink-muted">选择图层查看其操作历史（样式/显隐/重排等）</p>
              ) : layerCommands.length === 0 ? (
                <p className="px-1 text-caption text-ink-muted" role="status">
                  该图层在当前撤销/重做栈内没有带图层归因的操作记录
                </p>
              ) : (
                <ul data-testid="history-layer-log">
                  {layerCommands.map((cmd) => (
                    <li key={cmd.id} className="flex items-center gap-2 px-2 py-1 text-body-sm text-ink-secondary">
                      <MapPin size={11} aria-hidden className="shrink-0 text-ink-muted" />
                      <span className="rounded-sm bg-surface-sunken px-1.5 py-0.5 text-caption text-ink-muted">
                        {KIND_LABEL[cmd.kind] ?? cmd.kind}
                      </span>
                      <span className="min-w-0 flex-1 truncate">{cmd.label}</span>
                      <span className="shrink-0 text-caption text-ink-muted">{new Date(cmd.ts).toLocaleTimeString('zh-CN')}</span>
                    </li>
                  ))}
                </ul>
              )}
              <p className="mt-2 px-1 text-caption text-ink-muted">
                注：仅撤销/重做栈内带图层归因的命令可在此追溯（样式/显隐/重排）；不可逆删除只在时间线 journal 留痕。
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

/**
 * Ctrl+Z 可见反馈：订阅 opsLog 头部，撤销/重做发生时 toast 播报（undo.ts
 * journal 自动记录 type=undo/redo —— 不接全局键也能反馈）。
 */
export function UndoFlash(): React.ReactElement | null {
  const head = useHudStore((s) => s.opsLog[0]);
  const lastId = React.useRef<string | null>(null);
  React.useEffect(() => {
    if (!head || head.id === lastId.current) return;
    const isFirst = lastId.current === null;
    lastId.current = head.id;
    if (isFirst) return; // 挂载时既有的日志头不播报
    if (head.type === 'undo') useToastStore.getState().addToast(head.label, 'info');
    if (head.type === 'redo') useToastStore.getState().addToast(head.label, 'info');
  }, [head]);
  return null;
}
