'use client';

/**
 * useKnowledgeDocs — 知识库文档目录（GET /knowledge/documents，offset 分页）。
 *
 * 面板 open 时拉取；requestSeq 丢弃陈旧响应（仓库惯例，cf. analysis-graph-panel）。
 * 删除成功后重置到第一页 —— 后端删除是软删 + 可能触发压实，本地挪移
 * offset 容易算错 total，重拉是最诚实的选择。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  deleteKnowledgeDocument,
  listKnowledgeDocuments,
  type KnowledgeDocument,
} from '@/lib/api/knowledge';

export interface UseKnowledgeDocsResult {
  docs: KnowledgeDocument[];
  total: number;
  /** 首屏加载中（面板刚打开） */
  loading: boolean;
  /** 追加页加载中 */
  loadingMore: boolean;
  error: string | null;
  /** 正在删除的文档 id */
  removingId: string | null;
  refresh: () => void;
  loadMore: () => void;
  remove: (documentId: string) => Promise<void>;
}

export function useKnowledgeDocs(open: boolean, pageSize = 20): UseKnowledgeDocsResult {
  const [docs, setDocs] = useState<KnowledgeDocument[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);

  const seqRef = useRef(0);
  const offsetRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      seqRef.current += 1;
    };
  }, []);

  const fetchPage = useCallback(
    async (mode: 'reset' | 'more') => {
      const seq = ++seqRef.current;
      const offset = mode === 'reset' ? 0 : offsetRef.current;
      if (mode === 'reset') setLoading(true);
      else setLoadingMore(true);
      setError(null);
      try {
        const page = await listKnowledgeDocuments({ limit: pageSize, offset });
        if (!mountedRef.current || seq !== seqRef.current) return;
        offsetRef.current = offset + page.items.length;
        setTotal(page.total);
        setDocs((prev) => (mode === 'reset' ? page.items : [...prev, ...page.items]));
      } catch (err) {
        if (!mountedRef.current || seq !== seqRef.current) return;
        setError(err instanceof Error ? err.message : '无法加载知识库文档');
      } finally {
        if (mountedRef.current && seq === seqRef.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    },
    [pageSize],
  );

  const refresh = useCallback(() => {
    void fetchPage('reset');
  }, [fetchPage]);

  const loadMore = useCallback(() => {
    if (loading || loadingMore || docs.length >= total) return;
    void fetchPage('more');
  }, [fetchPage, loading, loadingMore, docs.length, total]);

  // 面板打开时拉取（关闭→打开之间数据可能已变化，每次打开都重置）。
  useEffect(() => {
    if (open) void fetchPage('reset');
  }, [open, fetchPage]);

  const remove = useCallback(
    async (documentId: string) => {
      setRemovingId(documentId);
      try {
        await deleteKnowledgeDocument(documentId);
        if (!mountedRef.current) return;
        void fetchPage('reset');
      } catch (err) {
        if (mountedRef.current) {
          setError(err instanceof Error ? err.message : '删除失败或无权访问该文档');
        }
      } finally {
        if (mountedRef.current) setRemovingId(null);
      }
    },
    [fetchPage],
  );

  return { docs, total, loading, loadingMore, error, removingId, refresh, loadMore, remove };
}

export default useKnowledgeDocs;
