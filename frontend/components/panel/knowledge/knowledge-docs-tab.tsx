'use client';

/**
 * KnowledgeDocsTab — 知识库文档目录（GET /knowledge/documents + DELETE）。
 *
 * 诚实性边界（契约见 frontend/docs/knowledge-market-recon.md）：
 * - 后端没有文档详情/分块列表端点 → 行不可展开，不装作可点开；
 * - status 实际只会是 completed（同步索引），其余词表按原文渲染不臆测；
 * - 删除仅 creator 可操作，失败原样报错。
 */
import { useState } from 'react';
import { BookOpen, RefreshCw, Trash2 } from 'lucide-react';
import EmptyState from '@/components/shared/empty-state';
import type { UseKnowledgeDocsResult } from '@/lib/hooks/use-knowledge-docs';
import type { KnowledgeDocument } from '@/lib/api/knowledge';

function StatusBadge({ status }: { status: string }) {
  const tone =
    status === 'completed'
      ? 'text-status-success'
      : status === 'failed'
        ? 'text-status-critical'
        : 'text-ink-muted';
  return <span className={`text-meta font-medium ${tone}`}>{status}</span>;
}

function formatTime(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

function DocRow({
  doc,
  removing,
  onDelete,
}: {
  doc: KnowledgeDocument;
  removing: boolean;
  onDelete: (id: string) => void;
}) {
  const [confirming, setConfirming] = useState(false);

  return (
    <li className="rounded-md border border-edge-subtle bg-surface-raised px-3 py-2">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-body font-medium text-ink">{doc.title}</span>
            {doc.file_type && (
              <span className="rounded-sm bg-surface-sunken px-1.5 py-0.5 text-meta text-ink-muted">
                {doc.file_type}
              </span>
            )}
          </div>
          <div className="mt-0.5 flex items-center gap-2 text-meta text-ink-muted">
            <span>{doc.chunk_count} 分块</span>
            <span aria-hidden>·</span>
            <StatusBadge status={doc.status} />
            <span aria-hidden>·</span>
            <span>{formatTime(doc.created_at)}</span>
          </div>
        </div>
        {!confirming ? (
          <button
            type="button"
            aria-label={`删除文档 ${doc.title}`}
            disabled={removing}
            onClick={() => setConfirming(true)}
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-sm text-ink-muted transition-colors hover:bg-surface-hover hover:text-status-critical disabled:opacity-50"
          >
            <Trash2 size={14} aria-hidden />
          </button>
        ) : (
          <span className="shrink-0 text-meta font-medium text-ink-muted">…</span>
        )}
      </div>
      {confirming && (
        <div
          role="alertdialog"
          aria-label={`确认删除 ${doc.title}`}
          className="mt-2 rounded-sm border border-edge-subtle bg-surface-sunken px-3 py-2"
        >
          <p className="text-meta text-ink-secondary">
            删除后该文档的向量立即不可检索；当删除占比 ≥20% 时后端会自动压实索引。
            仅文档创建者可删除。
          </p>
          <div className="mt-2 flex gap-2">
            <button
              type="button"
              disabled={removing}
              aria-busy={removing}
              onClick={() => onDelete(doc.id)}
              className="rounded-sm bg-status-critical px-2.5 py-1 text-meta font-medium text-white transition-opacity hover:opacity-85 disabled:opacity-50"
            >
              {removing ? '删除中…' : '确认删除'}
            </button>
            <button
              type="button"
              disabled={removing}
              onClick={() => setConfirming(false)}
              className="rounded-sm border border-edge-subtle px-2.5 py-1 text-meta font-medium text-ink-secondary transition-colors hover:bg-surface-hover disabled:opacity-50"
            >
              取消
            </button>
          </div>
        </div>
      )}
    </li>
  );
}

export function KnowledgeDocsTab({ docsResult }: { docsResult: UseKnowledgeDocsResult }) {
  const { docs, total, loading, loadingMore, error, removingId, refresh, loadMore, remove } =
    docsResult;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold">
          文档目录{!loading && !error && `（共 ${total} 篇）`}
        </div>
        <button
          type="button"
          onClick={refresh}
          aria-label="刷新文档列表"
          disabled={loading}
          className="inline-flex items-center gap-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-meta font-medium text-ink-secondary transition-colors hover:bg-surface-hover disabled:opacity-50"
        >
          <RefreshCw size={12} aria-hidden className={loading ? 'animate-spin' : ''} />
          刷新
        </button>
      </div>

      {error && (
        <p role="alert" className="text-body font-medium text-status-critical">
          {error}
        </p>
      )}

      {loading ? (
        <p className="py-4 text-center text-body text-ink-muted italic">加载中…</p>
      ) : docs.length === 0 && !error ? (
        <EmptyState
          icon={BookOpen}
          title="暂无已索引文档"
          description="在上方录入并索引一篇文档后，这里会出现文档目录。"
        />
      ) : (
        <ul className="flex flex-col gap-1.5">
          {docs.map((doc) => (
            <DocRow
              key={doc.id}
              doc={doc}
              removing={removingId === doc.id}
              onDelete={(id) => void remove(id)}
            />
          ))}
        </ul>
      )}

      {docs.length > 0 && docs.length < total && (
        <button
          type="button"
          onClick={loadMore}
          disabled={loadingMore}
          className="rounded-sm border border-edge-subtle bg-surface-sunken px-3 py-1.5 text-meta font-medium text-ink-secondary transition-colors hover:bg-surface-hover disabled:opacity-50"
        >
          {loadingMore ? '加载中…' : `加载更多（已显示 ${docs.length} / ${total}）`}
        </button>
      )}
    </div>
  );
}

export default KnowledgeDocsTab;
