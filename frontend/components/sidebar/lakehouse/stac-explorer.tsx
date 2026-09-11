'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ChevronLeft, ChevronRight, Map as MapIcon, Satellite } from 'lucide-react';
import {
  lakehouseApi,
  type StacCatalogResult,
  type StacItem,
} from '@/lib/api/lakehouse';
import { useHudStore } from '@/lib/store/useHudStore';
import { useToastStore } from '@/components/ui/toast';
import { EmptyState } from '@/components/shared/empty-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { LoadingState } from '@/components/shared/loading-state';
import { STitle } from '@/components/shared/section-title';

export interface StacExplorerProps {
  ownerType: 'session' | 'project';
  ownerId: string;
  sessionId: string;
  ownerToken?: string | null;
}

const PAGE_SIZE = 20;

/**
 * STAC 目录浏览器（P5）：时空检索 + 条目/资产浏览 + 几何上图。
 *
 * 后端是 STAC 1.0.0 Collection 投影（分页 links 有界；bbox/time_start 缺失
 * 的条目诚实进 skipped）。检索参数 = owner 域 + 分页（投影端点不接关键词/
 * 时间过滤 —— 与 catalog 检索面分工，客户端按 datetime/kind 收窄）。
 */
export function StacExplorer({ ownerType, ownerId, sessionId, ownerToken }: StacExplorerProps) {
  const [result, setResult] = useState<StacCatalogResult | null>(null);
  const [offset, setOffset] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [keyword, setKeyword] = useState('');
  const [timeFrom, setTimeFrom] = useState('');
  const [timeTo, setTimeTo] = useState('');
  const [selected, setSelected] = useState<StacItem | null>(null);

  const addLayer = useHudStore((s) => s.addLayer);
  const addToast = useToastStore((s) => s.addToast);
  const reqRef = useRef<{ controller: AbortController | null; seq: number }>({
    controller: null,
    seq: 0,
  });

  const load = useCallback(async () => {
    if (!ownerId || (ownerType === 'session' && !sessionId)) {
      setResult(null);
      return;
    }
    const seq = ++reqRef.current.seq;
    reqRef.current.controller?.abort();
    const controller = new AbortController();
    reqRef.current.controller = controller;
    setLoading(true);
    setError(null);
    try {
      const res = await lakehouseApi.searchCatalogStac(
        {
          owner_type: ownerType,
          owner_id: ownerId,
          session_id: ownerType === 'session' ? sessionId : undefined,
          limit: PAGE_SIZE,
          offset,
        },
        { ownerToken, signal: controller.signal },
      );
      if (seq !== reqRef.current.seq) return;
      setResult(res);
      setSelected(null);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
      if (seq !== reqRef.current.seq) return;
      setError(e instanceof Error ? e.message : '获取 STAC 目录失败');
    } finally {
      if (seq === reqRef.current.seq) setLoading(false);
    }
  }, [ownerType, ownerId, sessionId, ownerToken, offset]);

  useEffect(() => {
    void load();
  }, [load]);

  // 卸载中止在途请求（ref 是稳定可变数据引用，读 .current 是刻意的）。
  useEffect(() => () => reqRef.current?.controller?.abort(), []);

  // 客户端时空收窄（关键词 + 时间范围；服务端投影端点只接分页 —— 勘察纪要
  // 协调点 §3：过滤参数未在 REST 面暴露）。
  const visibleItems =
    result?.items.filter((it) => {
      if (keyword.trim()) {
        const hay = `${it.id} ${it.properties['webgis:kind']} ${it.properties['webgis:tags']?.join(' ') ?? ''}`;
        if (!hay.toLowerCase().includes(keyword.trim().toLowerCase())) return false;
      }
      const day = it.properties.datetime.slice(0, 10);
      if (timeFrom && day < timeFrom) return false;
      if (timeTo && day > timeTo) return false;
      return true;
    }) ?? [];

  const mountGeometry = useCallback(
    (item: StacItem) => {
      const fc = {
        type: 'FeatureCollection' as const,
        features: [
          {
            type: 'Feature' as const,
            geometry: item.geometry,
            properties: { id: item.id, datetime: item.properties.datetime },
          },
        ],
      };
      addLayer({
        id: `lakehouse-stac-${Date.now()}`,
        name: `STAC · ${item.id.slice(0, 12)}`,
        type: 'vector',
        visible: true,
        opacity: 1,
        group: 'reference',
        source: fc as unknown as Parameters<typeof addLayer>[0]['source'],
        provenance: { result_ref: 'lakehouse-stac' },
      });
      addToast('STAC 条目几何已上图', 'success');
    },
    [addLayer, addToast],
  );

  if (!ownerId) {
    return (
      <EmptyState
        icon={Satellite}
        title={ownerType === 'project' ? '请先填写项目 ID' : '暂无活跃会话'}
        description="STAC 投影按 owner 域隔离。"
      />
    );
  }

  const nextOffset = parseLinkOffset(result?.collection.links, 'next');
  const prevOffset = offset > 0 ? Math.max(0, offset - PAGE_SIZE) : null;

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-panel py-2" data-testid="lakehouse-stac">
      {error && <InlineNotice variant="error">{error}</InlineNotice>}
      {loading && <LoadingState label="正在获取 STAC 目录…" />}

      {result && !loading && (
        <>
          <div className="rounded-md border border-edge-subtle bg-surface-overlay px-panel py-2">
            <STitle title="Collection" sub={result.collection.id} />
            <div className="space-y-1 text-caption">
              <div className="flex justify-between gap-2">
                <span className="text-ink-muted">STAC 版本</span>
                <span className="font-mono text-ink">{result.collection.stac_version}</span>
              </div>
              {result.collection.extent?.temporal?.interval?.[0] && (
                <div className="flex justify-between gap-2">
                  <span className="text-ink-muted">时间范围</span>
                  <span className="text-ink">
                    {result.collection.extent.temporal.interval[0].map((t) => t?.slice(0, 10) ?? '…').join(' → ')}
                  </span>
                </div>
              )}
              {result.skipped.length > 0 && (
                <div className="pt-1 text-micro text-status-warning">
                  {result.skipped.length} 条无法投影（缺 bbox/时间）—— 诚实披露不静默丢弃
                </div>
              )}
            </div>
          </div>

          <input
            type="text"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            placeholder="按 id / 类型 / 标签收窄…"
            aria-label="STAC 条目关键词"
            className="mt-2 w-full rounded-sm border border-edge-subtle bg-surface-sunken px-2 py-1 text-caption text-ink"
          />
          <div className="mt-1.5 flex items-center gap-2 text-caption text-ink-secondary">
            <label className="flex items-center gap-1">
              从
              <input
                type="date"
                value={timeFrom}
                onChange={(e) => setTimeFrom(e.target.value)}
                aria-label="时间范围起始"
                className="rounded-sm border border-edge-subtle bg-surface-sunken px-1.5 py-1 text-ink"
              />
            </label>
            <label className="flex items-center gap-1">
              至
              <input
                type="date"
                value={timeTo}
                onChange={(e) => setTimeTo(e.target.value)}
                aria-label="时间范围结束"
                className="rounded-sm border border-edge-subtle bg-surface-sunken px-1.5 py-1 text-ink"
              />
            </label>
          </div>

          <ul className="mt-2 space-y-1.5" data-testid="lakehouse-stac-items">
            {visibleItems.map((item) => (
              <li key={item.id}>
                <button
                  type="button"
                  onClick={() => setSelected(selected?.id === item.id ? null : item)}
                  className="w-full rounded-md border border-l-2 border-edge-subtle border-l-transparent bg-surface-overlay px-panel py-2 text-left transition-colors hover:bg-surface-hover"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate font-mono text-caption text-ink">{item.id.slice(0, 16)}</span>
                    <span className="shrink-0 rounded-sm bg-surface-sunken px-1.5 py-0.5 font-mono text-micro text-ink-secondary">
                      {item.properties['webgis:kind']}
                    </span>
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 text-micro text-ink-muted">
                    <span>{item.properties.datetime.slice(0, 10)}</span>
                    <span className="font-mono">
                      {item.bbox.map((n) => n.toFixed(1)).join(', ')}
                    </span>
                  </div>
                </button>
                {selected?.id === item.id && (
                  <div className="mx-panel mb-1 rounded-md border border-edge-subtle bg-surface-sunken p-2" data-testid="lakehouse-stac-detail">
                    <STitle title="资产（assets）" />
                    <ul className="space-y-1">
                      {Object.entries(item.assets).map(([key, asset]) => (
                        <li key={key} className="flex items-center justify-between gap-2 text-micro">
                          <span className="font-mono text-ink-secondary">{key}</span>
                          <span className="truncate font-mono text-ink-muted" title={asset.href}>
                            {asset.href}
                          </span>
                        </li>
                      ))}
                    </ul>
                    <button
                      type="button"
                      onClick={() => mountGeometry(item)}
                      className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-sm bg-status-accent px-2.5 py-1.5 text-caption font-medium text-ink-on-accent transition-opacity hover:opacity-85"
                    >
                      <MapIcon size={12} aria-hidden />
                      几何上图
                    </button>
                  </div>
                )}
              </li>
            ))}
          </ul>
          {visibleItems.length === 0 && (
            <EmptyState icon={Satellite} title="无可投影条目" description="该域内没有同时具备 bbox 与时间的条目。" />
          )}

          {(prevOffset !== null || nextOffset !== null) && (
            <div className="flex items-center justify-between py-2 text-caption text-ink-secondary">
              <button
                type="button"
                disabled={prevOffset === null}
                onClick={() => setOffset(prevOffset ?? 0)}
                className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 transition-colors hover:bg-surface-hover disabled:opacity-40"
              >
                <ChevronLeft size={12} aria-hidden /> 上一页
              </button>
              <span className="font-mono">offset {offset}</span>
              <button
                type="button"
                disabled={nextOffset === null}
                onClick={() => setOffset(nextOffset ?? offset)}
                className="flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 transition-colors hover:bg-surface-hover disabled:opacity-40"
              >
                下一页 <ChevronRight size={12} aria-hidden />
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

/** 从 collection links 解析 rel=next 的 offset（有界分页）。 */
function parseLinkOffset(links: Array<{ rel: string; href: string }> | undefined, rel: string): number | null {
  const link = links?.find((l) => l.rel === rel);
  if (!link) return null;
  const match = /[?&]offset=(\d+)/.exec(link.href);
  return match ? Number(match[1]) : null;
}
