'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { ArrowLeft } from 'lucide-react';
import {
  lakehouseApi,
  type CatalogEntry,
  type LakehouseManifest,
} from '@/lib/api/lakehouse';
import { describeApiError } from '@/lib/api/transport';
import { LoadingState } from '@/components/shared/loading-state';
import { InlineNotice } from '@/components/shared/inline-notice';
import { STitle as SectionTitle } from '@/components/shared/section-title';

export interface ObjectDetailPanelProps {
  entry: CatalogEntry;
  sessionId: string;
  ownerToken?: string | null;
  onBack: () => void;
  /** 血缘入口（P8 面板在此 tab 内渲染）。 */
  onOpenLineage: (objectId: string) => void;
}

const KIND_LABEL: Record<string, string> = {
  zarr_cube: 'Zarr Cube',
  vector_parquet: '矢量 Parquet',
  cog_raster: 'COG 栅格',
  arrow_ipc: 'Arrow IPC',
  virtual: '虚拟对象',
  modelops_artifact: 'ModelOps 产物',
};

function formatBytes(n: number): string {
  if (n >= 1 << 30) return `${(n / (1 << 30)).toFixed(2)} GB`;
  if (n >= 1 << 20) return `${(n / (1 << 20)).toFixed(1)} MB`;
  if (n >= 1 << 10) return `${(n / (1 << 10)).toFixed(1)} KB`;
  return `${n} B`;
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-2 py-1 text-caption">
      <span className="shrink-0 text-ink-muted">{label}</span>
      <span className="min-w-0 break-all text-right text-ink">{children}</span>
    </div>
  );
}

/**
 * DataObject manifest 只读检视（owner 校验后的诚实形态）。payload 是任意
 * 业务载荷 —— 以 JSON 树呈现但不臆测语义；服务器路径类字段不展示。
 */
export function ObjectDetailPanel({
  entry,
  sessionId,
  ownerToken,
  onBack,
  onOpenLineage,
}: ObjectDetailPanelProps) {
  const [manifest, setManifest] = useState<LakehouseManifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reqRef = useRef<{ controller: AbortController | null; seq: number }>({
    controller: null,
    seq: 0,
  });

  const load = useCallback(async () => {
    const seq = ++reqRef.current.seq;
    reqRef.current.controller?.abort();
    const controller = new AbortController();
    reqRef.current.controller = controller;
    setLoading(true);
    setError(null);
    try {
      const res = await lakehouseApi.getObject(entry.object_id, sessionId, {
        ownerToken,
        signal: controller.signal,
      });
      if (seq !== reqRef.current.seq) return;
      setManifest(res.manifest);
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
      if (seq !== reqRef.current.seq) return;
      setError(describeApiError(e, '获取对象 manifest 失败'));
    } finally {
      if (seq === reqRef.current.seq) setLoading(false);
    }
  }, [entry.object_id, sessionId, ownerToken]);

  useEffect(() => {
    void load();
  }, [load]);

  // 卸载中止在途请求（ref 是稳定可变数据引用，读 .current 是刻意的）。
  useEffect(() => () => reqRef.current?.controller?.abort(), []);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-panel py-2" data-testid="lakehouse-object-detail">
      <button
        type="button"
        onClick={onBack}
        className="mb-2 flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-1 text-caption text-ink-secondary transition-colors hover:bg-surface-hover"
      >
        <ArrowLeft size={12} aria-hidden />
        返回目录
      </button>
      {loading && <LoadingState label="正在读取 manifest…" />}
      {error && <InlineNotice variant="error">{error}</InlineNotice>}
      {manifest && (
        <div className="space-y-3">
          <SectionTitle title="manifest" />
          <div className="divide-y divide-edge-subtle rounded-md border border-edge-subtle bg-surface-overlay px-panel py-1">
            <Row label="类型">{KIND_LABEL[manifest.kind] ?? manifest.kind}</Row>
            <Row label="对象 ID">
              <span className="font-mono text-micro">{manifest.content_sha256.slice(0, 16)}…</span>
            </Row>
            <Row label="大小">{formatBytes(manifest.byte_size)}</Row>
            <Row label="内容摘要">
              <span className="font-mono text-micro">{manifest.content_sha256.slice(0, 24)}…</span>
            </Row>
            <Row label="环境指纹">
              <span className="font-mono text-micro">{manifest.environment_fingerprint.slice(0, 16)}…</span>
            </Row>
            <Row label="来源引用">
              {manifest.source_refs.length === 0
                ? '（无）'
                : `${manifest.source_refs.length} 个上游`}
            </Row>
          </div>

          <SectionTitle title="内容块" />
          {manifest.content_blobs.length === 0 ? (
            <p className="px-panel text-caption text-ink-muted">
              无内容块（virtual 对象 —— 内容在子对象上）。
            </p>
          ) : (
            <ul className="space-y-1 px-panel">
              {manifest.content_blobs.map((b) => (
                <li
                  key={b.path}
                  className="flex items-center justify-between gap-2 rounded-sm bg-surface-sunken px-2 py-1 font-mono text-micro text-ink-secondary"
                >
                  <span className="truncate">{b.path.split('/').pop()}</span>
                  <span className="shrink-0">{formatBytes(b.byte_size)}</span>
                </li>
              ))}
            </ul>
          )}

          <SectionTitle title="业务载荷（payload）" />
          <pre className="max-h-48 overflow-auto rounded-md border border-edge-subtle bg-surface-sunken p-2 font-mono text-micro text-ink-secondary">
            {JSON.stringify(manifest.payload, null, 2)}
          </pre>

          <button
            type="button"
            onClick={() => onOpenLineage(entry.object_id)}
            className="w-full rounded-sm bg-status-accent px-2.5 py-1.5 text-caption font-medium text-ink-on-accent transition-opacity hover:opacity-85"
          >
            查看血缘链
          </button>
        </div>
      )}
    </div>
  );
}
