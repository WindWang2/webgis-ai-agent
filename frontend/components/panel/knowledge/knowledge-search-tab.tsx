'use client';

/**
 * KnowledgeSearchTab — 语义检索（GET /knowledge/search）+「注入对话」。
 *
 * 契约事实（frontend/docs/knowledge-market-recon.md）：
 * - score 是 FAISS L2 距离，越小越相关 —— 原样展示并标注语义，
 *   不归一化、不伪装成相似度百分比。
 * - 命中自带分块全文（后端无截断预览字段），展开即全文。
 * - chat API 的 message 是纯字符串、无结构化附件字段（ADR-0145），
 *   因此「注入对话」= 把带 citation 标记（[n] + 来源文档 + 分块 id）的文本
 *   拼入聊天输入框草稿，由用户确认后发送，不静默代发。
 */
import { useCallback, useRef, useState, type ReactNode } from 'react';
import { Search } from 'lucide-react';
import EmptyState from '@/components/shared/empty-state';
import { searchKnowledge, type KnowledgeSearchHit } from '@/lib/api/knowledge';
import { useHudStore } from '@/lib/store/useHudStore';

const TOP_K_CHOICES = [1, 3, 5, 10, 20] as const;
/** 注入草稿时单片段截断长度（聊天消息体积约束，截断可见为 …）。 */
const INJECT_EXCERPT_CHARS = 600;
/** 内容超过该字符数才显示「展开全文」按钮（短内容无需展开）。 */
const PREVIEW_COLLAPSE_CHARS = 160;

type SearchState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'ready'; results: KnowledgeSearchHit[] }
  | { status: 'error'; message: string };

/** 高亮命中词：区间合并后切分，不用正则 test（/g lastIndex 陷阱）。 */
function highlightContent(text: string, query: string): ReactNode {
  const terms = [...new Set(query.trim().toLowerCase().split(/\s+/).filter(Boolean))];
  if (terms.length === 0) return text;
  const lower = text.toLowerCase();
  const ranges: Array<[number, number]> = [];
  for (const term of terms) {
    let idx = lower.indexOf(term);
    while (idx !== -1) {
      ranges.push([idx, idx + term.length]);
      idx = lower.indexOf(term, idx + term.length);
    }
  }
  if (ranges.length === 0) return text;
  ranges.sort((a, b) => a[0] - b[0]);
  const merged: Array<[number, number]> = [ranges[0]];
  for (const [start, end] of ranges.slice(1)) {
    const last = merged[merged.length - 1];
    if (start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  const nodes: ReactNode[] = [];
  let cursor = 0;
  merged.forEach(([start, end], i) => {
    if (start > cursor) nodes.push(text.slice(cursor, start));
    nodes.push(<mark key={i} className="rounded-sm bg-status-accent/25 text-ink">{text.slice(start, end)}</mark>);
    cursor = end;
  });
  if (cursor < text.length) nodes.push(text.slice(cursor));
  return nodes;
}

/** 把命中片段组装成带 citation 标记的聊天草稿文本。 */
export function composeCitationDraft(hits: KnowledgeSearchHit[], query: string): string {
  const blocks = hits.map((h, i) => {
    const excerpt =
      h.content.length > INJECT_EXCERPT_CHARS
        ? `${h.content.slice(0, INJECT_EXCERPT_CHARS)}…`
        : h.content;
    return `[${i + 1}] 来源：《${h.title}》 分块 ${h.id}（L2 ${h.score.toFixed(3)}）\n${excerpt}`;
  });
  // 头部行尾带行内编号标记：citation.tsx 会把它们渲染成可点击角标，
  // 让「问题 ↔ 来源」的对应关系在气泡里直接可见。
  const markers = hits.map((_, i) => `[${i + 1}]`).join('');
  return `请基于以下知识库片段回答：${query} ${markers}\n\n${blocks.join('\n\n')}`;
}

function HitCard({
  hit,
  query,
  onInject,
}: {
  hit: KnowledgeSearchHit;
  query: string;
  onInject: (hit: KnowledgeSearchHit) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <li className="rounded-md border border-edge-subtle bg-surface-raised px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="truncate text-body font-medium text-ink">{hit.title}</div>
          <div className="mt-0.5 text-meta text-ink-muted">
            分块 {hit.id} · L2 距离 {hit.score.toFixed(4)}
            <span className="ml-1 text-ink-muted/80">（越小越相关）</span>
          </div>
        </div>
        <button
          type="button"
          onClick={() => onInject(hit)}
          className="shrink-0 rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-meta font-medium text-ink-secondary transition-colors hover:bg-surface-hover"
        >
          注入对话
        </button>
      </div>
      <p
        className={`mt-1.5 whitespace-pre-wrap text-body leading-relaxed text-ink-secondary ${
          expanded ? '' : 'line-clamp-3'
        }`}
      >
        {highlightContent(hit.content, query)}
      </p>
      {hit.content.length > PREVIEW_COLLAPSE_CHARS && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          aria-expanded={expanded}
          className="mt-1 text-meta font-medium text-status-accent underline underline-offset-2"
        >
          {expanded ? '收起' : '展开全文'}
        </button>
      )}
    </li>
  );
}

export function KnowledgeSearchTab({ onRequestClose }: { onRequestClose: () => void }) {
  const [query, setQuery] = useState('');
  const [topK, setTopK] = useState<number>(5);
  const [state, setState] = useState<SearchState>({ status: 'idle' });
  const [lastQuery, setLastQuery] = useState('');
  const [injectNote, setInjectNote] = useState<string | null>(null);

  const seqRef = useRef(0);
  const setPendingChatInjection = useHudStore((s) => s.setPendingChatInjection);

  const runSearch = useCallback(() => {
    const q = query.trim();
    if (!q) return;
    const seq = ++seqRef.current;
    setLastQuery(q);
    setState({ status: 'loading' });
    setInjectNote(null);
    searchKnowledge({ q, topK })
      .then((results) => {
        if (seq !== seqRef.current) return;
        setState({ status: 'ready', results });
      })
      .catch((err: unknown) => {
        if (seq !== seqRef.current) return;
        setState({
          status: 'error',
          message: err instanceof Error ? err.message : '检索失败',
        });
      });
  }, [query, topK]);

  const inject = useCallback(
    (hits: KnowledgeSearchHit[]) => {
      setPendingChatInjection(composeCitationDraft(hits, lastQuery));
      setInjectNote(
        hits.length > 1
          ? `已把 ${hits.length} 个片段（[1]–[${hits.length}]）拼入聊天输入框，请确认后发送。`
          : '已把该片段（[1]）拼入聊天输入框，请确认后发送。',
      );
      onRequestClose();
    },
    [lastQuery, setPendingChatInjection, onRequestClose],
  );

  return (
    <div className="flex flex-col gap-3">
      <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold">
        语义检索
      </div>

      <form
        className="flex items-center gap-2"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          runSearch();
        }}
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="检索查询"
          placeholder="用自然语言描述要找的内容…"
          className="h-8 flex-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2.5 text-body text-ink placeholder:text-ink-muted focus:outline-none focus:ring-1 focus:ring-status-accent"
        />
        <label htmlFor="knowledge-topk" className="text-meta text-ink-muted">
          Top
        </label>
        <select
          id="knowledge-topk"
          value={topK}
          onChange={(e) => setTopK(Number(e.target.value))}
          className="h-8 rounded-sm border border-edge-subtle bg-surface-sunken px-1.5 text-body text-ink focus:outline-none focus:ring-1 focus:ring-status-accent"
        >
          {TOP_K_CHOICES.map((k) => (
            <option key={k} value={k}>
              {k}
            </option>
          ))}
        </select>
        <button
          type="submit"
          disabled={!query.trim() || state.status === 'loading'}
          aria-busy={state.status === 'loading'}
          className="inline-flex h-8 items-center gap-1.5 rounded-sm bg-status-accent px-3 text-body font-medium text-ink-on-accent transition-opacity hover:opacity-85 disabled:opacity-50"
        >
          <Search size={13} aria-hidden />
          {state.status === 'loading' ? '检索中…' : '检索'}
        </button>
      </form>

      {state.status === 'ready' && state.results.length === 0 && (
        <EmptyState
          icon={Search}
          title="无检索命中"
          description={`「${lastQuery}」在已索引文档中没有命中。试着换一种表述，或先索引相关文档。`}
        />
      )}
      {state.status === 'error' && (
        <p role="alert" className="text-body font-medium text-status-critical">
          {state.message}
        </p>
      )}
      {state.status === 'ready' && state.results.length > 0 && (
        <>
          <div className="flex items-center justify-between">
            <p className="text-meta text-ink-muted">
              命中 {state.results.length} 条 · score 为 FAISS L2 距离（越小越相关），非相似度百分比
            </p>
            <button
              type="button"
              onClick={() => inject(state.results)}
              className="rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-meta font-medium text-ink-secondary transition-colors hover:bg-surface-hover"
            >
              全部注入对话
            </button>
          </div>
          <ul className="flex flex-col gap-2">
            {state.results.map((hit) => (
              <HitCard key={hit.id} hit={hit} query={lastQuery} onInject={(h) => inject([h])} />
            ))}
          </ul>
        </>
      )}

      {injectNote && (
        <p role="status" className="text-body font-medium text-status-success">
          {injectNote}
        </p>
      )}
    </div>
  );
}

export default KnowledgeSearchTab;
