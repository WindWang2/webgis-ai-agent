'use client';

/**
 * citation — 知识库引用（[n] 角标 + 来源卡）共享渲染件（ADR-0145）。
 *
 * 注入格式（由 KnowledgeSearchTab.composeCitationDraft 产出）：
 *
 *   请基于以下知识库片段回答：{query}
 *
 *   [1] 来源：《{title}》 分块 {chunkId}（L2 {score}）
 *   {excerpt}
 *
 *   [2] …
 *
 * 渲染策略（零回归原则）：
 * - 定义块从正文剥离，集中渲染为文末「引用来源」列表（摘录不再以原文
 *   形式混入正文，正文可读性不回退）；
 * - 正文中「已知编号」的 `[n]` 转成 markdown 链接 `[[n]](#cite-n)`，由
 *   components.a 拦截渲染为角标按钮 —— 不引 raw HTML / rehype-raw，
 *   不扩大 XSS 面；
 * - 没有定义块的消息（全部存量内容）正文逐字节不变，锚点行为不变。
 *   摘录的终结边界是「下一个定义行或文末」：用户在注入文本后追加的
 *   自由文字会被并入最后一个来源的摘录，只影响卡片摘录展示，不影响数据。
 */
import {
  createContext,
  useContext,
  useId,
  useState,
  type ReactNode,
} from 'react';
import { BookOpen, X } from 'lucide-react';
import { useHudStore } from '@/lib/store/useHudStore';

export interface CitationSource {
  n: number;
  title: string;
  chunkId: string;
  /** 分数原文（L2 距离），不归一化 */
  scoreLabel: string;
  excerpt: string;
}

const DEF_RE = /^\[(\d+)\] 来源：《([^》]+)》 分块 (\S+)（L2 ([\d.]+)）\s*$/;

export function splitCitationBlocks(text: string): { body: string; sources: CitationSource[] } {
  // 快路径：无定义块的正文（全部存量消息）直接原样返回。
  if (!text.includes('] 来源：《')) return { body: text, sources: [] };
  const lines = text.split('\n');
  const sources: CitationSource[] = [];
  const kept: string[] = [];
  let i = 0;
  while (i < lines.length) {
    const m = DEF_RE.exec(lines[i]);
    if (!m) {
      kept.push(lines[i]);
      i += 1;
      continue;
    }
    const source: CitationSource = {
      n: Number(m[1]),
      title: m[2],
      chunkId: m[3],
      scoreLabel: m[4],
      excerpt: '',
    };
    i += 1;
    const excerptLines: string[] = [];
    while (i < lines.length && !DEF_RE.test(lines[i])) {
      excerptLines.push(lines[i]);
      i += 1;
    }
    while (excerptLines.length && excerptLines[excerptLines.length - 1].trim() === '') {
      excerptLines.pop();
    }
    source.excerpt = excerptLines.join('\n').trim();
    sources.push(source);
  }
  const body = kept.join('\n').replace(/\n{3,}/g, '\n\n').trim();
  return { body, sources };
}

/** 把正文中已知编号的 `[n]` 转成 citation 锚点链接；未知编号原样保留。 */
export function citeTextPreprocess(body: string, sources: CitationSource[]): string {
  if (sources.length === 0) return body;
  const known = new Set(sources.map((s) => s.n));
  return body.replace(/\[(\d+)\]/g, (raw, num: string) => {
    const n = Number(num);
    return known.has(n) ? `[[${n}]](#cite-${n})` : raw;
  });
}

export function isCitationHref(href?: string): boolean {
  return typeof href === 'string' && /^#cite-\d+$/.test(href);
}

const CitationSourcesContext = createContext<CitationSource[]>([]);

export function CitationSourcesProvider({
  sources,
  children,
}: {
  sources: CitationSource[];
  children: ReactNode;
}) {
  return (
    <CitationSourcesContext.Provider value={sources}>{children}</CitationSourcesContext.Provider>
  );
}

function CitationCard({
  cardId,
  source,
  onClose,
}: {
  cardId: string;
  source: CitationSource;
  onClose: () => void;
}) {
  const setRagPanelOpen = useHudStore((s) => s.setRagPanelOpen);
  return (
    <div
      id={cardId}
      role="dialog"
      aria-label={`引用来源 ${source.n}：${source.title}`}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.stopPropagation();
          onClose();
        }
      }}
      className="absolute bottom-full right-0 z-50 mb-1.5 w-72 rounded-md border border-edge-subtle bg-surface-panel p-3 text-left shadow-drawer"
    >
      <div className="flex items-start justify-between gap-2">
        <p className="text-body font-semibold leading-snug text-ink">《{source.title}》</p>
        <button
          type="button"
          aria-label="关闭引用卡片"
          onClick={onClose}
          className="shrink-0 rounded-sm p-0.5 text-ink-muted transition-colors hover:bg-surface-hover hover:text-ink"
        >
          <X size={12} aria-hidden />
        </button>
      </div>
      <p className="mt-0.5 text-meta text-ink-muted">
        分块 {source.chunkId} · L2 距离 {source.scoreLabel}（越小越相关）
      </p>
      {source.excerpt && (
        <p className="mt-1.5 max-h-40 overflow-y-auto whitespace-pre-wrap text-meta leading-relaxed text-ink-secondary">
          {source.excerpt}
        </p>
      )}
      <button
        type="button"
        onClick={() => {
          onClose();
          setRagPanelOpen(true);
        }}
        className="mt-2 inline-flex items-center gap-1 text-meta font-medium text-status-accent underline underline-offset-2"
      >
        <BookOpen size={11} aria-hidden />
        在知识库面板打开
      </button>
    </div>
  );
}

/** components.a 拦截点：`#cite-n` → 角标按钮；由 CitationSourcesProvider 提供来源。 */
export function CitationAnchor({ href }: { href: string }) {
  const sources = useContext(CitationSourcesContext);
  const [open, setOpen] = useState(false);
  const cardId = useId();
  const n = Number(href.slice('#cite-'.length));
  const source = sources.find((s) => s.n === n) ?? null;
  if (!source) return <sup>[{n}]</sup>;
  return (
    <sup
      className="relative inline-block"
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-describedby={open ? cardId : undefined}
        aria-label={`引用来源 ${n}：${source.title}`}
        onClick={(e) => {
          e.preventDefault();
          setOpen((v) => !v);
        }}
        className="mx-0.5 align-super text-[0.72em] font-semibold leading-none text-status-accent underline decoration-dotted decoration-1 underline-offset-2 hover:text-status-accent-vivid"
      >
        [{n}]
      </button>
      {open && <CitationCard cardId={cardId} source={source} onClose={() => setOpen(false)} />}
    </sup>
  );
}

/** 文末「引用来源」列表：注入块剥离后的集中展示（键盘/读屏可达的静态版）。 */
export function CitationSourceList({ sources }: { sources: CitationSource[] }) {
  if (sources.length === 0) return null;
  return (
    <div
      data-state="citations"
      className="mt-2 rounded-md border border-edge-subtle bg-surface-sunken/40 px-3 py-2"
    >
      <div className="mb-1 text-meta font-semibold uppercase tracking-wider text-ink-muted">
        引用来源
      </div>
      <ol className="flex flex-col gap-1.5">
        {sources.map((s) => (
          <li key={s.n} className="text-meta leading-relaxed text-ink-secondary">
            <span className="font-semibold text-status-accent">[{s.n}]</span> 《{s.title}》 · 分块{' '}
            {s.chunkId} · L2 {s.scoreLabel}
            {s.excerpt && (
              <p className="mt-0.5 line-clamp-2 whitespace-pre-wrap text-ink-muted">{s.excerpt}</p>
            )}
          </li>
        ))}
      </ol>
    </div>
  );
}

/**
 * CitationArea — story-markdown 的 citation 扩展点（契约 §8：≤60 行消费方）。
 * renderMarkdown 收到剥离/预处理后的正文，由调用方保留自己的渲染配方。
 */
export function CitationArea({
  text,
  renderMarkdown,
}: {
  text: string;
  renderMarkdown: (body: string) => ReactNode;
}) {
  const { body, sources } = splitCitationBlocks(text);
  const prepared = citeTextPreprocess(body, sources);
  return (
    <CitationSourcesProvider sources={sources}>
      {renderMarkdown(prepared)}
      <CitationSourceList sources={sources} />
    </CitationSourcesProvider>
  );
}
