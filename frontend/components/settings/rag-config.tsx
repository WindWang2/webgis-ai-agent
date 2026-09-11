'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { STitle, SField, SButton } from '@/components/shared/section-title';
import { apiFetch, isApiError, describeApiError } from '@/lib/api/transport';
import { searchKnowledge, type KnowledgeSearchHit } from '@/lib/api/knowledge';

interface BackendDoc {
  id: string;
  title: string;
  file_type?: string;
  chunk_count?: number;
  status?: string;
  created_at?: string | null;
}

type DocsState =
  | { status: 'loading' }
  | { status: 'ready'; total: number; items: BackendDoc[] }
  | { status: 'error'; message: string };

type TestRetrievalState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'done'; query: string; hits: KnowledgeSearchHit[] }
  | { status: 'error'; message: string };

/**
 * 分数分布条 — 原始 L2 距离的线性相对长度（相对最差命中），
 * 不归一化成"相似度百分比"（诚实呈现：L2 越小越相关，条越短越好）。
 */
function ScoreDistribution({ hits }: { hits: KnowledgeSearchHit[] }) {
  const max = Math.max(...hits.map((h) => h.score));
  const min = Math.min(...hits.map((h) => h.score));
  return (
    <div
      role="img"
      aria-label={`命中分数分布：${hits.length} 条，L2 距离范围 ${min.toFixed(3)} 到 ${max.toFixed(3)}`}
      data-state="score-distribution"
      className="flex flex-col gap-1"
    >
      {hits.map((h, i) => (
        <div key={h.id} className="flex items-center gap-2">
          <span className="w-5 shrink-0 text-meta text-ink-muted">[{i + 1}]</span>
          <div
            className="h-2 min-w-[2px] rounded-sm bg-status-accent/70"
            style={{ width: `${max > 0 ? Math.max((h.score / max) * 100, 1.5) : 1.5}%` }}
          />
          <span className="shrink-0 text-meta text-ink-muted">{h.score.toFixed(4)}</span>
        </div>
      ))}
      <p className="mt-1 text-meta text-ink-muted">
        条长 ∝ L2 距离（相对最差命中的线性比例）。L2 距离越小越相关，非相似度百分比。
      </p>
    </div>
  );
}

/**
 * RAG 配置（#551 修复）。
 *
 * 此前的 Retrieval Config 滑杆（Spatial Weight / Top K / Rerank）写入持久化的
 * ragConfig，但后端没有任何消费方 —— 对话引擎/检索端点不读这些值（后端
 * top_k 是请求级参数，且此 UI 从不发起检索请求），滑杆是假控件，已移除。
 *
 * 文档索引区此前渲染 ragSpatial/ragSemantic（零生产者，永远为空数组，固定
 * 显示 "No ... indexed yet"）—— 改为真实消费后端能力：GET
 * /api/v1/knowledge/documents 列出当前用户/组织的已索引文档。
 *
 * Vector DB Address/Collection 标注"仅供展示"（后端当前使用内置本地 FAISS，
 * 不依赖外部向量库）；Test Connection 走真实的 /api/v1/config/rag/test。
 *
 * V9（ADR-0145）：新增测试检索（真实 GET /knowledge/search + 命中分数分布）
 * 与「打开知识库面板」导航；嵌入模型无读写端点，以诚实提示呈现（不硬编码
 * 模型名 —— 它是后端代码常量，UI 显示会漂移）。
 */
export function RagConfig() {
  const ragConfig = useHudStore((s) => s.ragConfig);
  const setRagConfig = useHudStore((s) => s.setRagConfig);
  const setRagPanelOpen = useHudStore((s) => s.setRagPanelOpen);

  const [vectorDb, setVectorDb] = useState(ragConfig.vectorDb);
  const [collection, setCollection] = useState(ragConfig.collection);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<
    'idle' | 'success' | 'error'
  >('idle');
  const [testDetail, setTestDetail] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const [docs, setDocs] = useState<DocsState>({ status: 'loading' });

  const [retrievalQuery, setRetrievalQuery] = useState('');
  const [retrieval, setRetrieval] = useState<TestRetrievalState>({ status: 'idle' });

  const timersRef = useRef<Set<ReturnType<typeof setTimeout>>>(new Set());

  useEffect(() => {
    const timers = timersRef.current;
    return () => {
      timers.forEach((t) => clearTimeout(t));
      timers.clear();
    };
  }, []);

  // 真实文档目录：GET /api/v1/knowledge/documents（租户隔离，当前用户/组织）。
  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        const res = await apiFetch<{ data: { total: number; items: BackendDoc[] } }>(
          '/api/v1/knowledge/documents?limit=50',
          { signal: controller.signal, label: 'Knowledge docs error' }
        );
        setDocs({
          status: 'ready',
          total: res.data?.total ?? 0,
          items: res.data?.items ?? [],
        });
      } catch (err) {
        if (controller.signal.aborted) return;
        setDocs({
          status: 'error',
          message: isApiError(err) && err.status === 401
            ? '需要登录后查看知识库文档'
            : describeApiError(err, '无法加载知识库文档'),
        });
      }
    })();
    return () => controller.abort();
  }, []);

  const addTimer = (cb: () => void, ms: number) => {
    const id = setTimeout(() => {
      timersRef.current.delete(id);
      cb();
    }, ms);
    timersRef.current.add(id);
    return id;
  };

  const handleSave = () => {
    setRagConfig({ vectorDb, collection });
    setSaved(true);
    addTimer(() => setSaved(false), 2000);
  };

  const handleTestConnection = async () => {
    setTesting(true);
    setTestResult('idle');
    setTestDetail(null);
    try {
      // #390：真实连通性测试。后端校验其实际使用的知识库存储
      // （内置本地 FAISS 向量库）；address/collection 一并发送，
      // 供后端在接入外部向量库时使用。之前这里是 setTimeout 1.2s
      // 后无条件置 success 的假测试。
      const data = await apiFetch<{ status: string; store?: string; detail?: string }>(
        '/api/v1/config/rag/test',
        {
          method: 'POST',
          body: { address: vectorDb, collection },
          label: 'RAG connectivity test error',
        }
      );
      setTestResult('success');
      setTestDetail(data.detail ?? null);
    } catch (err) {
      setTestResult('error');
      setTestDetail(
        isApiError(err) && err.status === 403
          ? '需要管理员权限才能测试连接'
          : describeApiError(err, '连接失败')
      );
    } finally {
      setTesting(false);
    }
  };

  const runTestRetrieval = useCallback(async () => {
    const q = retrievalQuery.trim();
    if (!q || retrieval.status === 'loading') return;
    setRetrieval({ status: 'loading' });
    try {
      // 真实语义检索（与知识库面板同一条端点），top_k=5。
      const hits = await searchKnowledge({ q, topK: 5 });
      setRetrieval({ status: 'done', query: q, hits });
    } catch (err) {
      setRetrieval({
        status: 'error',
        message: err instanceof Error ? err.message : '检索失败',
      });
    }
  }, [retrievalQuery, retrieval.status]);

  return (
    <div className="flex flex-col gap-5">
      <STitle title="知识库 · RAG" sub="Retrieval-Augmented Generation" />

      {/* Indexed documents — 真实目录（原 Spatial/Semantic Index 展示的是
          零生产者的空数组假状态，已移除，改为后端真实列表） */}
      <div>
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-2">
          Indexed Documents
        </div>
        {docs.status === 'loading' && (
          <div className="text-body text-ink-muted italic py-2">Loading…</div>
        )}
        {docs.status === 'error' && (
          <div className="text-body font-medium text-status-critical py-2">
            {docs.message}
          </div>
        )}
        {docs.status === 'ready' && docs.items.length === 0 && (
          <div className="text-body text-ink-muted italic py-2">
            暂无已索引文档（{docs.total} 篇）
          </div>
        )}
        {docs.status === 'ready' && docs.items.length > 0 && (
          <div className="flex flex-col gap-1.5">
            {docs.items.map((doc) => (
              <div
                key={doc.id}
                className="flex items-center gap-3 rounded-md border border-edge-subtle bg-surface-raised px-3 py-2"
              >
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-body font-medium text-ink truncate">
                      {doc.title}
                    </span>
                    {doc.file_type && (
                      <span className="rounded-sm bg-surface-sunken px-1.5 py-0.5 text-body text-ink-muted">
                        {doc.file_type}
                      </span>
                    )}
                  </div>
                  <div className="text-body text-ink-muted mt-0.5">
                    {doc.chunk_count != null && `${doc.chunk_count} chunks`}
                    {doc.chunk_count != null && ' · '}
                    {doc.status}
                  </div>
                </div>
              </div>
            ))}
            {docs.total > docs.items.length && (
              <div className="text-body text-ink-muted italic pt-1">
                共 {docs.total} 篇（仅显示前 {docs.items.length} 篇）
              </div>
            )}
          </div>
        )}
      </div>

      {/* Test Retrieval — 真实语义检索 + 命中分数分布（V9） */}
      <div>
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-3">
          Test Retrieval · 测试检索
        </div>
        <div className="flex flex-col gap-3 rounded-md border border-edge-subtle bg-surface-raised px-4 py-3">
          <form
            className="flex items-center gap-2"
            role="search"
            onSubmit={(e) => {
              e.preventDefault();
              void runTestRetrieval();
            }}
          >
            <input
              value={retrievalQuery}
              onChange={(e) => setRetrievalQuery(e.target.value)}
              aria-label="测试检索查询"
              placeholder="输入查询，查看检索命中与分数分布…"
              className="h-7 flex-1 rounded-sm border border-edge-subtle bg-surface-sunken px-2 text-body text-ink placeholder:text-ink-muted focus:outline-none focus:ring-1 focus:ring-status-accent"
            />
            <button
              type="submit"
              disabled={!retrievalQuery.trim() || retrieval.status === 'loading'}
              aria-busy={retrieval.status === 'loading'}
              className="inline-flex items-center gap-1.5 rounded-sm border border-edge-subtle bg-surface-sunken px-3 py-1 text-body font-medium text-ink-secondary transition-all hover:bg-surface-hover disabled:opacity-50"
            >
              {retrieval.status === 'loading' ? '检索中…' : '检索'}
            </button>
          </form>

          {retrieval.status === 'done' && retrieval.hits.length === 0 && (
            <p className="text-body text-ink-muted italic">
              无命中 ——「{retrieval.query}」在当前已索引文档中没有结果。
            </p>
          )}
          {retrieval.status === 'error' && (
            <p role="alert" className="text-body font-medium text-status-critical">
              {retrieval.message}
            </p>
          )}
          {retrieval.status === 'done' && retrieval.hits.length > 0 && (
            <div className="flex flex-col gap-3">
              <ScoreDistribution hits={retrieval.hits} />
              <ul className="flex flex-col gap-1.5">
                {retrieval.hits.map((hit, i) => (
                  <li
                    key={hit.id}
                    className="rounded-sm border border-edge-subtle bg-surface-sunken px-2.5 py-1.5"
                  >
                    <div className="flex items-center gap-2 text-meta text-ink-secondary">
                      <span className="font-semibold text-status-accent">[{i + 1}]</span>
                      <span className="truncate font-medium text-ink">{hit.title}</span>
                      <span className="ml-auto shrink-0 text-ink-muted">
                        分块 {hit.id} · L2 {hit.score.toFixed(4)}
                      </span>
                    </div>
                    <p className="mt-0.5 line-clamp-2 text-meta text-ink-muted">{hit.content}</p>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <p className="text-meta text-ink-muted">
            嵌入模型由后端固定（代码常量，无读写配置端点）；此处仅探测检索效果。
          </p>
        </div>
      </div>

      {/* 知识库面板入口（V9 导航整合：设置页 → 面板） */}
      <div className="flex items-center gap-3 pt-1">
        <button
          type="button"
          onClick={() => setRagPanelOpen(true)}
          className="inline-flex items-center gap-1.5 rounded-sm border border-edge-subtle bg-surface-sunken px-3 py-1.5 text-body font-medium text-ink-secondary transition-all hover:bg-surface-hover"
        >
          打开知识库面板
        </button>
        <span className="text-meta text-ink-muted">上传 / 删除文档与注入对话在面板中完成</span>
      </div>

      {/* Vector DB connection */}
      <div>
        <div className="text-heading uppercase tracking-wider text-ink-muted font-semibold mb-3">
          Vector DB Connection
        </div>
        <div className="flex flex-col gap-3 rounded-md border border-edge-subtle bg-surface-raised px-4 py-3">
          <SField
            label="Address"
            value={vectorDb}
            onChange={setVectorDb}
            placeholder="http://localhost:19530"
            hint="仅供展示：当前后端使用内置本地向量库（FAISS），不依赖外部向量数据库。"
          />
          <SField
            label="Collection"
            value={collection}
            onChange={setCollection}
            placeholder="geoagent"
            hint="仅供展示：保存不会改变后端检索行为。"
          />
          <div className="flex items-center gap-3">
            <button
              onClick={handleTestConnection}
              disabled={testing}
              className="inline-flex items-center gap-1.5 rounded-sm border border-edge-subtle bg-surface-sunken px-3 py-1 text-body font-medium text-ink-secondary transition-all hover:bg-surface-hover disabled:opacity-50"
            >
              {testing ? 'Testing...' : 'Test Connection'}
            </button>
            {testResult === 'success' && (
              <span className="text-body font-medium text-status-success">
                Connected
              </span>
            )}
            {testResult === 'error' && (
              <span className="text-body font-medium text-status-critical">
                Failed
              </span>
            )}
          </div>
          {testResult === 'success' && testDetail && (
            <div className="text-body text-ink-muted">{testDetail}</div>
          )}
          {testResult === 'error' && (
            <div className="text-body font-medium text-status-critical">
              {testDetail ? `连接失败：${testDetail}` : '连接失败'}
            </div>
          )}
        </div>
      </div>

      {/* Save */}
      <div className="pt-2">
        <SButton saved={saved} onClick={handleSave}>
          {saved ? 'Saved' : 'Save'}
        </SButton>
      </div>
    </div>
  );
}