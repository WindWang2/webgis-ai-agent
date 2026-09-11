'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { Clock, History, Star } from 'lucide-react';
import { lakehouseApi } from '@/lib/api/lakehouse';
import type {
  CubeBuildResult,
  CubeWindowResult,
  LabeledWindowResult,
  VectorScanResult,
} from '@/lib/api/lakehouse';
import { useHudStore } from '@/lib/store/useHudStore';
import { useToastStore } from '@/components/ui/toast';
import { useLakehouseHistory, type LakehouseQueryRecord } from '@/lib/hooks/use-lakehouse-history';
import { EmptyState } from '@/components/shared/empty-state';
import { QueryForm, EMPTY_FORM, buildRequest, type QueryFormValue } from './query-forms';
import { QueryResults } from './query-results';

export interface QueryPaneProps {
  sessionId: string;
  ownerToken?: string | null;
  /** 目录「查询」动线：catalog 条目 object_id（尝试经 manifest payload 解析 ref）。 */
  objectIdHint?: string | null;
  onHintConsumed?: () => void;
}

/**
 * 查询页编排（P4）：表单 → schema 预校验 → 执行 → 历史/收藏 → 结果
 * （统计 + 直方图）→ 上图（window/labeled 帧 → HeatmapRasterSource 通道；
 * scan → GeoJSON 直挂 ≤5000 要素契约）/ 时序播放器入口。
 */
export function QueryPane({ sessionId, ownerToken, objectIdHint, onHintConsumed }: QueryPaneProps) {
  const [form, setForm] = useState<QueryFormValue>(EMPTY_FORM);
  const [submitting, setSubmitting] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [hint, setHint] = useState<string | null>(null);

  const [windowResult, setWindowResult] = useState<CubeWindowResult | null>(null);
  const [labeledResult, setLabeledResult] = useState<LabeledWindowResult | null>(null);
  const [scanResult, setScanResult] = useState<VectorScanResult | null>(null);
  const [buildResult, setBuildResult] = useState<CubeBuildResult | null>(null);
  const [labeledRequestBbox, setLabeledRequestBbox] = useState<[number, number, number, number] | null>(null);
  const [timelineSource, setTimelineSource] = useState<CubeWindowResult | null>(null);

  const addLayer = useHudStore((s) => s.addLayer);
  const addToast = useToastStore((s) => s.addToast);
  const { history, favorites, record, toggleFavorite, isFavorite } = useLakehouseHistory();

  const reqRef = useRef<{ controller: AbortController | null }>({ controller: null });
  // 卸载中止在途查询（tab 切换即卸载 —— ContextPanel 语义）。
  useEffect(() => () => reqRef.current?.controller?.abort(), []);

  // 目录动线：object_id → manifest payload.ref 解析（解析不出诚实提示）。
  useEffect(() => {
    if (!objectIdHint) return;
    let cancelled = false;
    setHint('正在从对象 manifest 解析 cube ref…');
    lakehouseApi
      .getObject(objectIdHint, sessionId, { ownerToken })
      .then((res) => {
        if (cancelled) return;
        const ref = res.manifest?.payload?.ref;
        if (typeof ref === 'string' && ref) {
          setForm((f) => ({ ...f, ref }));
          setHint(`已解析 ref：${ref}`);
        } else {
          setHint('该对象 manifest 未携带 cube ref —— 请手输 ref:cube/…（ref 与 object id 是不同身份）');
        }
      })
      .catch(() => {
        if (!cancelled) setHint('对象解析失败 —— 请手输 ref:cube/…');
      })
      .finally(() => {
        onHintConsumed?.();
      });
    return () => {
      cancelled = true;
    };
  }, [objectIdHint, sessionId, ownerToken, onHintConsumed]);

  const execute = useCallback(async () => {
    setFormError(null);
    const built = buildRequest(form, sessionId);
    if (built.error || !built.request) {
      setFormError(built.error ?? '表单校验失败');
      return;
    }
    const req = built.request;
    const controller = new AbortController();
    reqRef.current.controller?.abort();
    reqRef.current.controller = controller;
    setSubmitting(true);
    try {
      if (req.kind === 'window') {
        const res = await lakehouseApi.readCubeWindow(
          req.payload as unknown as Parameters<typeof lakehouseApi.readCubeWindow>[0],
          { ownerToken, signal: controller.signal },
        );
        setWindowResult(res);
        setLabeledResult(null);
        setScanResult(null);
        setBuildResult(null);
      } else if (req.kind === 'labeled') {
        const res = await lakehouseApi.readLabeledWindow(
          req.payload as unknown as Parameters<typeof lakehouseApi.readLabeledWindow>[0],
          { ownerToken, signal: controller.signal },
        );
        setLabeledResult(res);
        setWindowResult(null);
        setScanResult(null);
        setBuildResult(null);
        setLabeledRequestBbox((req.payload.bbox as [number, number, number, number]) ?? null);
      } else if (req.kind === 'scan') {
        const res = await lakehouseApi.scanVector(
          req.payload as unknown as Parameters<typeof lakehouseApi.scanVector>[0],
          { ownerToken, signal: controller.signal },
        );
        setScanResult(res);
        setWindowResult(null);
        setLabeledResult(null);
        setBuildResult(null);
      } else if (req.kind === 'revise') {
        const res = await lakehouseApi.reviseCube(
          req.payload as unknown as Parameters<typeof lakehouseApi.reviseCube>[0],
          { ownerToken, signal: controller.signal },
        );
        setBuildResult(res);
        setWindowResult(null);
        setLabeledResult(null);
        setScanResult(null);
      } else {
        const res = await lakehouseApi.buildRsCube(
          req.payload as unknown as Parameters<typeof lakehouseApi.buildRsCube>[0],
          { ownerToken, signal: controller.signal },
        );
        setBuildResult(res);
        setWindowResult(null);
        setLabeledResult(null);
        setScanResult(null);
      }
      record({ kind: req.kind, ref: req.ref, label: req.label, request: req.payload });
    } catch (e) {
      if (e instanceof DOMException && e.name === 'AbortError') return;
      addToast(e instanceof Error ? e.message : '查询失败', 'error');
    } finally {
      setSubmitting(false);
    }
  }, [form, sessionId, ownerToken, record, addToast]);

  const mountFrame = useCallback(
    (frame: { grid: number[][]; bbox: [number, number, number, number] | null; title: string; nodata: number | null }) => {
      if (!frame.bbox) {
        addToast('窗口缺 transform / bbox —— 无法定位上图范围', 'error');
        return;
      }
      // raster-canvas 的 dataURL 需要 canvas 2d；不可用时诚实报错不静默。
      import('@/lib/map-kit/raster-canvas')
        .then(({ gridToRasterSource }) => {
          const source = gridToRasterSource(frame.grid, frame.bbox as [number, number, number, number], {
            nodata: frame.nodata,
          });
          if (!source) {
            addToast('当前环境无法渲染栅格位图', 'error');
            return;
          }
          addLayer({
            id: `lakehouse-frame-${Date.now()}`,
            name: `数据湖 · ${frame.title}`,
            type: 'heatmap',
            visible: true,
            opacity: 0.85,
            group: 'analysis',
            source,
            provenance: { result_ref: 'lakehouse-query' },
          });
          addToast('已上图（HeatmapRasterSource 通道）', 'success');
        })
        .catch(() => addToast('渲染模块加载失败', 'error'));
    },
    [addLayer, addToast],
  );

  const mountVector = useCallback(
    (fc: VectorScanResult, title: string) => {
      addLayer({
        id: `lakehouse-scan-${Date.now()}`,
        name: `数据湖 · ${title}`,
        type: 'vector',
        visible: true,
        opacity: 1,
        group: 'analysis',
        source: fc as unknown as Parameters<typeof addLayer>[0]['source'],
        provenance: { result_ref: 'lakehouse-scan' },
      });
      addToast('矢量结果已上图', 'success');
    },
    [addLayer, addToast],
  );

  const replayRecord = useCallback(
    (rec: LakehouseQueryRecord) => {
      setForm((f) => {
        const base = { ...f, ref: rec.ref };
        if (rec.kind === 'window') {
          const p = rec.request as { time?: [number, number]; y?: [number, number]; x?: [number, number] };
          return { ...base, mode: 'window', window: {
            time: p.time ? p.time.join(', ') : '',
            y: p.y ? p.y.join(', ') : '',
            x: p.x ? p.x.join(', ') : '',
          } };
        }
        return base;
      });
    },
    [],
  );

  const hasResult = windowResult || labeledResult || scanResult || buildResult;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <QueryForm
        value={form}
        onChange={setForm}
        onSubmit={() => void execute()}
        submitting={submitting}
        error={formError}
        hint={hint}
      />
      {hasResult ? (
        <QueryResults
          windowResult={windowResult}
          labeledResult={labeledResult}
          scanResult={scanResult}
          buildResult={buildResult}
          onMountToMap={mountFrame}
          onOpenTimeline={setTimelineSource}
          onMountVector={mountVector}
          labeledRequestBbox={labeledRequestBbox}
        />
      ) : (
        <EmptyState
          icon={History}
          title="执行一次查询"
          description="窗口读至少一个有限切片；标签读至少一种选择。结果可统计、上图、播放。"
        />
      )}
      {timelineSource && (
        <TimelineDialog result={timelineSource} onClose={() => setTimelineSource(null)} />
      )}
      {/* 历史 / 收藏 */}
      <div className="shrink-0 border-t border-edge-subtle px-panel py-1.5" data-testid="lakehouse-query-history">
        <div className="flex items-center gap-2 text-caption text-ink-secondary">
          <Clock size={12} aria-hidden />
          <span>历史 {history.length} · 收藏 {favorites.length}</span>
          {history.length > 0 && (
            <button
              type="button"
              onClick={() => replayRecord(history[0])}
              className="ml-auto rounded-sm bg-surface-sunken px-1.5 py-0.5 text-micro transition-colors hover:bg-surface-hover"
              data-testid="lakehouse-replay-latest"
            >
              重放最近
            </button>
          )}
        </div>
        {history.slice(0, 3).map((rec) => (
          <div key={rec.id} className="flex items-center gap-1 py-0.5 text-micro text-ink-muted">
            <button
              type="button"
              onClick={() => replayRecord(rec)}
              className="min-w-0 flex-1 truncate text-left hover:text-ink"
              title={`${rec.label}（点击回填表单）`}
            >
              [{rec.kind}] {rec.label}
            </button>
            <button
              type="button"
              aria-label={isFavorite(rec) ? '取消收藏' : '收藏'}
              onClick={() => toggleFavorite(rec)}
              className={isFavorite(rec) ? 'text-status-warning' : 'text-ink-muted hover:text-ink'}
            >
              <Star size={11} aria-hidden fill={isFavorite(rec) ? 'currentColor' : 'none'} />
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

/** 时序播放器对话框（P7 组件在本文件的挂载点；实现见 timeline-player.tsx）。 */
function TimelineDialog({ result, onClose }: { result: CubeWindowResult; onClose: () => void }) {
  const [Player, setPlayer] = useState<React.ComponentType<{ result: CubeWindowResult; onClose: () => void }> | null>(null);
  useEffect(() => {
    import('./timeline-player')
      .then((m) => setPlayer(() => m.TimelinePlayer))
      .catch(() => setPlayer(null));
  }, []);
  return (
    <div
      role="dialog"
      aria-label="时序播放器"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
    >
      <div
        className="max-h-[80vh] w-full max-w-lg overflow-auto rounded-md border border-edge-subtle bg-surface-panel p-3 shadow-overlay"
        onClick={(e) => e.stopPropagation()}
      >
        {Player ? (
          <Player result={result} onClose={onClose} />
        ) : (
          <EmptyState icon={History} title="播放器加载中…" description="模块动态载入。" />
        )}
      </div>
    </div>
  );
}
