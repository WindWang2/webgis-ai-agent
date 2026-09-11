'use client';

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Search, RefreshCw, MessageSquare, Layers as LayersIcon, Package, History, X } from 'lucide-react';
import { apiFetch } from '@/lib/api/transport';
import { useHudStore } from '@/lib/store/useHudStore';
import { useDialogFocus } from '@/lib/hooks/use-dialog-focus';
import { useToastStore } from '@/components/ui/toast';
import { useSearchDrawerStore } from '@/lib/hooks/use-search-drawer';
import { setPendingLocate } from '@/lib/search/locate';
import {
  buildIndexFromSessions,
  defaultSessionFetcher,
  loadPersistedSessions,
  normalizeSessionList,
  searchIndexStats,
  searchLocalIndex,
  type IndexProgress,
  type SearchHit,
  type SessionMeta,
} from '@/lib/search/session-index';

/**
 * 跨会话搜索面板（ADR-0147 P4）。
 *
 * 边界（诚实声明）：后端无全文端点，索引= 本地持久化（最近 20 会话，
 * LRU+体积预算）。打开时自动补索引最近会话；命中分四组：
 * 会话 / 消息 / 产物 / 当前工作区图层。消息与产物命中跳转恢复会话并
 * 定位到对应消息。
 */

interface SearchDrawerProps {
  /** 打开历史会话（复用工作区既有恢复管线）。 */
  onSelectSession: (sessionId: string) => void;
}

function useOpen(): boolean {
  return useSearchDrawerStore((s) => s.isOpen);
}
function useClose(): () => void {
  return useSearchDrawerStore((s) => s.closeDrawer);
}

const GROUP_META: Record<SearchHit['kind'], { label: string; Icon: typeof MessageSquare }> = {
  session: { label: '会话', Icon: History },
  message: { label: '消息', Icon: MessageSquare },
  artifact: { label: '产物', Icon: Package },
  layer: { label: '图层（当前工作区）', Icon: LayersIcon },
};

export function SearchDrawer({ onSelectSession }: SearchDrawerProps): React.ReactElement | null {
  const open = useOpen();
  const close = useClose();
  const [query, setQuery] = useState('');
  const [sessions, setSessions] = useState(() => loadPersistedSessions());
  const [progress, setProgress] = useState<IndexProgress | null>(null);
  const rebuildRef = useRef<AbortController | null>(null);

  const layers = useHudStore((s) => s.layers);
  const containerRef = useRef<HTMLDivElement>(null);

  useDialogFocus({ open, containerRef, onEscape: close, initialFocusSelector: 'input' });

  // 打开时后台补索引（可取消；完成/中断都刷新本地快照）
  const rebuild = useCallback(() => {
    rebuildRef.current?.abort();
    const controller = new AbortController();
    rebuildRef.current = controller;
    setProgress({ done: 0, total: 0 });
    (async () => {
      try {
        const list = await defaultSessionList(controller.signal);
        if (!controller.signal.aborted) {
          const indexed = await buildIndexFromSessions(list, (sid) => defaultSessionFetcher(sid), {
            signal: controller.signal,
            onProgress: setProgress,
          });
          setSessions(indexed);
        }
      } catch {
        // 会话列表拉取失败（匿名/离线）：本地缓存仍可搜
      } finally {
        if (!controller.signal.aborted) setProgress(null);
      }
    })();
  }, []);

  useEffect(() => {
    if (!open) return;
    setQuery('');
    setSessions(loadPersistedSessions());
    rebuild();
    return () => rebuildRef.current?.abort();
  }, [open, rebuild]);

  const hits = useMemo(() => {
    const layerHitsSrc = layers.map((l) => ({ id: l.id, name: l.name }));
    return searchLocalIndex(query, sessions, layerHitsSrc);
  }, [query, sessions, layers]);

  const handleSelect = useCallback(
    (hit: SearchHit) => {
      if (hit.kind === 'layer') {
        useHudStore.getState().setActiveLeftTab('layers');
        useToastStore.getState().addToast(`已在图层面板定位：${hit.layerName}`, 'success');
        close();
        return;
      }
      if (hit.kind === 'session') {
        onSelectSession(hit.sessionId);
        close();
        return;
      }
      // message / artifact：恢复会话并定位到消息
      if (typeof hit.messageIndex === 'number') {
        setPendingLocate(hit.sessionId, hit.messageIndex);
      }
      onSelectSession(hit.sessionId);
      close();
    },
    [onSelectSession, close],
  );

  if (!open) return null;

  const grouped = new Map<SearchHit['kind'], SearchHit[]>();
  for (const hit of hits) {
    const list = grouped.get(hit.kind);
    if (list) list.push(hit);
    else grouped.set(hit.kind, [hit]);
  }
  const stats = searchIndexStats();

  return (
    <div
      className="fixed inset-0 z-[80] flex items-start justify-center bg-black/40 p-4 pt-[8vh]"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
      data-testid="search-drawer-overlay"
    >
      <div
        ref={containerRef}
        role="dialog"
        aria-modal="true"
        aria-label="跨会话搜索"
        data-testid="search-drawer"
        className="flex max-h-[80vh] w-full max-w-[680px] flex-col overflow-hidden rounded-lg border border-edge-subtle bg-surface-raised shadow-2xl"
      >
        <div className="flex items-center gap-2 border-b border-edge-subtle px-4 py-3">
          <Search size={15} aria-hidden className="shrink-0 text-ink-muted" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="搜索会话 / 消息全文 / 产物 / 当前图层…"
            aria-label="跨会话搜索"
            className="w-full bg-transparent text-body text-ink outline-none placeholder:text-ink-muted"
            data-testid="search-input"
          />
          <button
            type="button"
            aria-label="重建索引"
            title="重新拉取最近会话并重建本地索引"
            onClick={rebuild}
            className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-ink"
          >
            <RefreshCw size={14} aria-hidden className={progress ? 'animate-spin' : ''} />
          </button>
          <button
            type="button"
            aria-label="关闭搜索"
            onClick={close}
            className="rounded-sm p-1 text-ink-muted hover:bg-surface-hover hover:text-ink"
          >
            <X size={15} aria-hidden />
          </button>
        </div>
        <div className="border-b border-edge-subtle px-4 py-1.5 text-caption text-ink-muted" data-testid="search-status">
          {progress
            ? `索引中 ${progress.done}/${progress.total}${progress.currentTitle ? `：${progress.currentTitle}` : ''}`
            : `本地索引：${stats.sessions} 会话 / ${stats.messages} 条消息（最近 20 会话，LRU 上限）`}
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
          {query.trim() === '' ? (
            <p className="py-8 text-center text-body-sm text-ink-muted">
              输入关键词在最近 20 个会话的全文中搜索；命中产物与会话可跳转恢复。
            </p>
          ) : hits.length === 0 ? (
            <p className="py-8 text-center text-body-sm text-ink-muted" role="status">
              没有匹配结果（仅覆盖已索引会话）
            </p>
          ) : (
            [...grouped.entries()].map(([kind, list]) => {
              const { label, Icon } = GROUP_META[kind];
              return (
                <section key={kind} className="py-1" data-testid={`search-group-${kind}`}>
                  <h3 className="px-2 pb-1 text-caption font-medium uppercase tracking-wide text-ink-muted">
                    <Icon size={11} aria-hidden className="mr-1 inline" />
                    {label}
                  </h3>
                  <ul>
                    {list.map((hit, i) => (
                      <li key={`${hit.sessionId}-${hit.kind}-${hit.messageIndex ?? 'x'}-${hit.ref ?? hit.layerId ?? i}`}>
                        <button
                          type="button"
                          onClick={() => handleSelect(hit)}
                          className="w-full rounded-md px-2 py-1.5 text-left hover:bg-surface-hover"
                          data-testid="search-hit"
                        >
                          <span className="block truncate text-body-sm text-ink">
                            {hit.kind === 'session' ? hit.sessionTitle : hit.kind === 'layer' ? hit.layerName : hit.snippet}
                          </span>
                          <span className="block truncate text-caption text-ink-muted">
                            {hit.kind === 'message' ? `${hit.sessionTitle} · ${hit.role === 'user' ? '提问' : '回答'}` : hit.kind === 'artifact' ? `${hit.sessionTitle} · ${hit.ref}` : hit.kind === 'session' ? '打开历史会话' : '当前工作区图层'}
                          </span>
                        </button>
                      </li>
                    ))}
                  </ul>
                </section>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}

async function defaultSessionList(signal: AbortSignal): Promise<SessionMeta[]> {
  const res = await apiFetch<{ sessions?: Array<{ id: string; title?: string; updatedAt?: number | string }> }>(
    '/api/v1/chat/sessions?limit=100',
    { signal, label: 'Cross-session list error' },
  );
  return normalizeSessionList(res);
}
