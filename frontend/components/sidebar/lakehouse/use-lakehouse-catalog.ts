'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import {
  lakehouseApi,
  type CatalogEntry,
  type CatalogOwnerType,
} from '@/lib/api/lakehouse';

/**
 * Lakehouse catalog 检索生命周期（use-spatial-catalog 同款竞态配方）：
 * - 过滤态即时更新，fetch 带 AbortController + 序号守卫，慢响应永不覆盖新结果；
 * - 卸载时中止最新在途请求；
 * - 分页用 offset 语义（后端 next_offset null = 无更多页）。
 *
 * 契约注记（勘察纪要 §1.3）：`total` 是字符串（">=10000" 形态的有界下界），
 * UI 直接呈现该诚实形态；REST 未暴露 bbox/tags 过滤参数，此处不提供。
 */

export const CATALOG_PAGE_SIZE = 50;

/** kind 词表（manifest kind 全集；'' = 全部）。 */
export const CATALOG_KIND_OPTIONS: Array<{ value: string; label: string }> = [
  { value: '', label: '全部类型' },
  { value: 'zarr_cube', label: 'Zarr Cube' },
  { value: 'vector_parquet', label: '矢量 Parquet' },
  { value: 'cog_raster', label: 'COG 栅格' },
  { value: 'arrow_ipc', label: 'Arrow IPC' },
  { value: 'virtual', label: '虚拟对象' },
  { value: 'modelops_artifact', label: 'ModelOps 产物' },
];

export interface CatalogFilters {
  kind: string;
  producer: string;
  timeFrom: string;
  timeTo: string;
  includeRevoked: boolean;
}

export const EMPTY_CATALOG_FILTERS: CatalogFilters = {
  kind: '',
  producer: '',
  timeFrom: '',
  timeTo: '',
  includeRevoked: false,
};

export interface LakehouseCatalogState {
  items: CatalogEntry[];
  total: string;
  totalBounded: boolean;
  count: number;
  nextOffset: number | null;
  loading: boolean;
  error: string | null;
}

export function useLakehouseCatalog(options: {
  ownerType: CatalogOwnerType;
  ownerId: string;
  sessionId: string;
  ownerToken?: string | null;
  filters: CatalogFilters;
  offset: number;
}) {
  const { ownerType, ownerId, sessionId, ownerToken, filters, offset } = options;
  const [state, setState] = useState<LakehouseCatalogState>({
    items: [],
    total: '0',
    totalBounded: true,
    count: 0,
    nextOffset: null,
    loading: false,
    error: null,
  });
  const reqRef = useRef<{ controller: AbortController | null; seq: number }>({
    controller: null,
    seq: 0,
  });

  const fetchPage = useCallback(async () => {
    // project 域还没有 owner id（或 session 域缺 sessionId）时不出请求。
    if (!ownerId || (ownerType === 'session' && !sessionId)) {
      setState((s) => ({ ...s, items: [], total: '0', count: 0, nextOffset: null, loading: false, error: null }));
      return;
    }
    const seq = ++reqRef.current.seq;
    reqRef.current.controller?.abort();
    const controller = new AbortController();
    reqRef.current.controller = controller;
    setState((s) => ({ ...s, loading: true, error: null }));
    try {
      const page = await lakehouseApi.searchCatalog(
        {
          owner_type: ownerType,
          owner_id: ownerId,
          session_id: ownerType === 'session' ? sessionId : undefined,
          kind: filters.kind || undefined,
          producer: filters.producer || undefined,
          time_from: filters.timeFrom || undefined,
          time_to: filters.timeTo || undefined,
          include_revoked: filters.includeRevoked,
          limit: CATALOG_PAGE_SIZE,
          offset,
        },
        { ownerToken, signal: controller.signal },
      );
      if (seq !== reqRef.current.seq) return;
      setState({
        items: page.items ?? [],
        total: page.total ?? '0',
        totalBounded: page.total_bounded ?? true,
        count: page.count ?? 0,
        nextOffset: page.next_offset ?? null,
        loading: false,
        error: null,
      });
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
      if (seq !== reqRef.current.seq) return;
      setState((s) => ({
        ...s,
        loading: false,
        error: e instanceof Error ? e.message : '获取 lakehouse 目录失败',
      }));
    }
  }, [ownerType, ownerId, sessionId, ownerToken, filters, offset]);

  useEffect(() => {
    void fetchPage();
  }, [fetchPage]);

  // 卸载中止（ref 是稳定可变数据引用，cleanup 里读 .current 是刻意的）。
  // 卸载中止在途请求（ref 是稳定可变数据引用，读 .current 是刻意的）。
  useEffect(() => () => reqRef.current?.controller?.abort(), []);

  return { ...state, refresh: () => void fetchPage() };
}
