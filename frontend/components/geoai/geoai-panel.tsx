'use client';
/**
 * GeoAI 可提示分割面板（Platform 11 / WP-G；ADR-0198）。
 *
 * 自包含面板：不 import 既有 map 组件（并行 track 所有权），预览经
 * GET /geoai/preview（PNG base64 + 地理元数据），提示绘制在 SVG 覆盖层
 * （点击 = 点提示；拖拽 = 框提示），提交 = GeoPrompt artifact（CRS 来自
 * 预览元数据）；候选掩膜按分数列出，接受 = POST /geoai/prompt-refine；
 * 撤销 = 弹出提示；批次队列 = 提交历史状态机（idle/running/done/error）。
 *
 * 可访问性（#2157/#2159/#2161/#2163）：全部控件走语义 token（明暗双主题
 * 对比度 ≥4.5:1）；根容器自持滚动区（全局 body overflow:hidden）；主操作
 * 应用 .touch-target（粗指针 44×44）；输入/select 有可编程名称；错误
 * role="alert"、候选/队列摘要 aria-live；叠加层可 Tab 聚焦，方向键移动
 * 光标、Enter 加点/定框、B 切换点/框模式（触屏用显式模式按钮，不依赖
 * Shift），拖拽走 setPointerCapture 且可取消；异步请求有统一 busy 与
 * 防重入，队列项无论上下文是否切换都在 finally 落终态。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { useT } from '@/lib/i18n/useT';
import { apiFetch, describeApiError, type ApiFetchOptions } from '@/lib/api/transport';
import { getMapSpecSessionCursor } from '@/lib/mapspec/session-cursor';
import { restorableSessionAnchor } from '@/lib/workbench/session-anchor';

import {
  type MapPrompt,
  type PreviewMeta,
  boxFromDrag,
  buildArtifactPayload,
  mapToScreen,
  screenToMap,
} from './geo-prompt-math';

interface GeoJsonFeature {
  type: 'Feature';
  properties: { candidate?: number; score?: number; source?: string };
  geometry: { type: string; coordinates: unknown };
}

interface CandidateSummary {
  index: number;
  score: number;
  source: string;
}

interface QueueItem {
  id: number;
  label: string;
  status: 'running' | 'done' | 'error';
  runId?: string;
  error?: string;
}

const CANDIDATE_COLORS = ['#22c55e', '#3b82f6', '#a855f7', '#f97316'];

/** 框提示手势：物理 Shift（桌面习惯）或显式框模式（键盘/触屏路径）。 */
type PromptMode = 'point' | 'box';

async function fetchJson(path: string, options?: ApiFetchOptions): Promise<unknown> {
  // 统一 transport（apiFetch）：认证头注入 + 401 刷新 + JSON 解析/错误归一。
  // 裸 fetch 在认证部署下整面板 401；2xx 非 JSON 体（网关错误页）会把
  // SyntaxError 原样抛给用户。路径为 API 相对路径（transport 自拼 API_BASE）。
  const cursor = getMapSpecSessionCursor();
  const sessionId = cursor.sessionId ?? restorableSessionAnchor()?.sessionId;
  if (!sessionId) throw new Error('Select a workspace session before using GeoAI.');
  const ownerToken = cursor.sessionId === sessionId ? cursor.ownerToken : null;
  if (options?.method === 'POST') {
    return apiFetch<unknown>(path, {
      ...options,
      ownerToken,
      body: { ...(options.body as Record<string, unknown>), session_id: sessionId },
    });
  }
  const query = new URLSearchParams({ session_id: sessionId });
  return apiFetch<unknown>(`${path}${path.includes('?') ? '&' : '?'}${query}`, { ...options, ownerToken });
}

const clamp = (v: number, lo: number, hi: number) => Math.min(hi, Math.max(lo, v));

export function GeoAiPanel() {
  const t = useT('geoai');
  const [sourceUri, setSourceUri] = useState('');
  const [models, setModels] = useState<{ model_id: string }[]>([]);
  const [modelId, setModelId] = useState('');
  const [modelsLoading, setModelsLoading] = useState(false);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [preview, setPreview] = useState<
    { png: string; meta: PreviewMeta } | null
  >(null);
  const [prompts, setPrompts] = useState<MapPrompt[]>([]);
  const [promptMode, setPromptMode] = useState<PromptMode>('point');
  const [candidates, setCandidates] = useState<CandidateSummary[]>([]);
  const [candidateFeatures, setCandidateFeatures] = useState<GeoJsonFeature[]>([]);
  const [selectedRun, setSelectedRun] = useState<{
    runId: string;
    candidatesPath?: string;
    modelId: string;
    sourceUri: string;
  } | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const dragRef = useRef<{ px: number; py: number } | null>(null);
  const [dragBox, setDragBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  // 键盘光标（预览像素坐标）；仅在叠加层聚焦时渲染。
  const [kbdCursor, setKbdCursor] = useState<{ px: number; py: number } | null>(null);
  const [kbdBoxStart, setKbdBoxStart] = useState<{ px: number; py: number } | null>(null);
  const [overlayFocused, setOverlayFocused] = useState(false);
  const seqRef = useRef(0);
  const busyRef = useRef(false);
  const contextRef = useRef('');
  const context = `${sourceUri}\u0000${modelId}`;
  contextRef.current = context;
  useEffect(() => {
    setCandidates([]);
    setCandidateFeatures([]);
    setSelectedRun(null);
    setPrompts([]);
  }, [context]);
  useEffect(() => { setPreview(null); }, [sourceUri]);


  const loadModels = useCallback(async () => {
    if (modelsLoading) return; // 防重入（#2163）
    setModelsLoading(true);
    try {
      const body = (await fetchJson(
        '/api/v1/geoai/models',
      )) as { models: { model_id: string; task_types: string[] }[] };
      const promptable = body.models.filter((m) =>
        m.task_types.includes('promptable_segmentation'),
      );
      setModels(promptable);
      if (promptable.length > 0 && !modelId) setModelId(promptable[0].model_id);
    } catch (exc) {
      setError(t('errors.modelsLoadFailed', { err: describeApiError(exc, t('errors.requestFailed')) }));
    } finally {
      setModelsLoading(false);
    }
  }, [modelId, modelsLoading, t]);

  const loadPreview = useCallback(async () => {
    if (previewLoading) return; // 防重入（#2163）
    const requestSource = sourceUri;
    setError('');
    setPreview(null);
    setCandidates([]);
    setCandidateFeatures([]);
    setSelectedRun(null);
    if (!sourceUri.trim()) {
      setError(t('errors.sourceRequired'));
      return;
    }
    setPreviewLoading(true);
    try {
      const body = (await fetchJson(
        `/api/v1/geoai/preview?source_uri=${encodeURIComponent(sourceUri.trim())}`,
      )) as { png_base64: string } & PreviewMeta;
      if (!contextRef.current.startsWith(`${requestSource}\u0000`)) return;
      setPreview({
        png: `data:image/png;base64,${body.png_base64}`,
        meta: {
          preview_width: body.preview_width,
          preview_height: body.preview_height,
          source_width: body.source_width,
          source_height: body.source_height,
          crs: body.crs,
          bounds: body.bounds,
        },
      });
    } catch (exc) {
      if (contextRef.current.startsWith(`${requestSource}\u0000`)) {
        setError(t('errors.previewLoadFailed', { err: describeApiError(exc, t('errors.requestFailed')) }));
      }
    } finally {
      setPreviewLoading(false);
    }
  }, [sourceUri, previewLoading, t]);

  const submit = useCallback(async () => {
    if (busyRef.current) return;
    if (!preview || prompts.length === 0 || !modelId) {
      setError(t('errors.submitPrecondition'));
      return;
    }
    busyRef.current = true;
    const requestContext = contextRef.current;
    setBusy(true);
    setError('');
    const id = ++seqRef.current;
    const label = `${modelId} · ${prompts.length}`;
    setQueue((q) => [...q, { id, label, status: 'running' as const }].slice(-20));
    try {
      const body = (await fetchJson('/api/v1/geoai/prompt-segment', {
        method: 'POST',
        body: {
          model_id: modelId,
          source_uri: sourceUri.trim(),
          artifact: buildArtifactPayload(prompts, preview.meta),
          return_candidates: true,
        },
      })) as {
        run_id: string;
        outputs: {
          prompt_candidates?: {
            path?: string;
            windows?: { candidates: { index: number; score: number; source: string }[] }[];
          };
        };
      };
      // #2163：队列终态必须在任何 early-return 之前写入 —— 上下文切换后
      // 旧请求返回不再让队列项永久 running。
      setQueue((q) =>
        q.map((item) =>
          item.id === id ? { ...item, status: 'done' as const, runId: body.run_id } : item,
        ),
      );
      if (contextRef.current !== requestContext) return; // 旧结果不渲染进新上下文
      const windows = body.outputs.prompt_candidates?.windows ?? [];
      // 多窗 run：每窗候选集同构——聚合平均分（展示语义）。
      const agg = new Map<number, { total: number; n: number; source: string }>();
      for (const w of windows) {
        for (const c of w.candidates ?? []) {
          const slot = agg.get(c.index) ?? { total: 0, n: 0, source: c.source };
          slot.total += c.score;
          slot.n += 1;
          slot.source = c.source;
          agg.set(c.index, slot);
        }
      }
      const summary: CandidateSummary[] = [...agg.entries()]
        .map(([index, slot]) => ({
          index,
          score: slot.total / Math.max(1, slot.n),
          source: slot.source,
        }))
        .sort((x, y) => x.index - y.index);
      setCandidates(summary);
      setSelectedRun({
        runId: body.run_id,
        modelId,
        sourceUri: sourceUri.trim(),
        candidatesPath: body.outputs.prompt_candidates?.path,
      });
      if (body.outputs.prompt_candidates?.path) {
        try {
          const geo = (await fetchJson(
            `/api/v1/geoai/artifact-geojson?path=${encodeURIComponent(
              body.outputs.prompt_candidates.path,
            )}`,
          )) as { features: GeoJsonFeature[] };
          if (contextRef.current === requestContext) setCandidateFeatures(geo.features ?? []);
        } catch {
          setCandidateFeatures([]);
        }
      } else {
        setCandidateFeatures([]);
      }
    } catch (exc) {
      setQueue((q) =>
        q.map((item) =>
          item.id === id ? { ...item, status: 'error' as const, error: describeApiError(exc, t('errors.requestFailed')) } : item,
        ),
      );
      // 旧上下文的失败不得污染当前错误区（#2163）。
      if (contextRef.current === requestContext) {
        setError(t('errors.submitFailed', { err: describeApiError(exc, t('errors.requestFailed')) }));
      }
    } finally {
      busyRef.current = false;
      setBusy(false);
    }
  }, [modelId, preview, prompts, sourceUri, t]);

  const acceptCandidate = useCallback(
    async (index: number) => {
      if (busyRef.current) return;
      if (!selectedRun?.candidatesPath) {
        setError(t('errors.refinePrecondition'));
        return;
      }
      busyRef.current = true;
      setBusy(true);
      try {
        const body = (await fetchJson('/api/v1/geoai/prompt-refine', {
          method: 'POST',
          body: {
            model_id: selectedRun.modelId,
            source_uri: selectedRun.sourceUri,
            candidates_path: selectedRun.candidatesPath,
            candidate: index,
          },
        })) as { run_id: string };
        const id = ++seqRef.current;
        setQueue((q) => [
          ...q,
          {
            id,
            label: t('refineLabel', { index }),
            status: 'done' as const,
            runId: body.run_id,
          },
        ].slice(-20));
      } catch (exc) {
        setError(t('errors.refineFailed', { err: describeApiError(exc, t('errors.requestFailed')) }));
      } finally {
        busyRef.current = false;
        setBusy(false);
      }
    },
    [selectedRun, t],
  );

  const undo = useCallback(() => {
    if (prompts.length > 0) {
      setPrompts((p) => p.slice(0, -1));
      return;
    }
    setCandidates([]);
    setCandidateFeatures([]);
    setSelectedRun(null);
  }, [prompts.length]);

  /** 预览像素坐标 → 地图坐标加点（指针点击与键盘 Enter 共用）。 */
  const addPointAt = useCallback((px: number, py: number) => {
    setPrompts((p) => {
      if (!preview) return p;
      const pt = screenToMap(px, py, preview.meta);
      return [...p, { kind: 'point' as const, x: pt.x, y: pt.y }];
    });
  }, [preview]);

  const cancelDrag = useCallback(() => {
    dragRef.current = null;
    setDragBox(null);
    setKbdBoxStart(null);
  }, []);

  const toPreviewCoords = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!preview) return { px: 0, py: 0 };
    const rect = e.currentTarget.getBoundingClientRect();
    const px = (e.clientX - rect.left) * preview.meta.preview_width / (rect.width || preview.meta.preview_width);
    const py = (e.clientY - rect.top) * preview.meta.preview_height / (rect.height || preview.meta.preview_height);
    return { px, py };
  };

  const onSvgPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!preview) return;
    const { px, py } = toPreviewCoords(e);
    if (e.shiftKey || promptMode === 'box') {
      // #2159：pointer capture 保证移出 SVG 后仍收到 up/cancel，状态不粘连。
      e.currentTarget.setPointerCapture?.(e.pointerId);
      dragRef.current = { px, py };
      setDragBox({ x: px, y: py, w: 0, h: 0 });
    } else {
      addPointAt(px, py);
    }
  };

  const onSvgPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!dragRef.current || !preview) return;
    const { px, py } = toPreviewCoords(e);
    const s = dragRef.current;
    setDragBox({
      x: Math.min(s.px, px),
      y: Math.min(s.py, py),
      w: Math.abs(px - s.px),
      h: Math.abs(py - s.py),
    });
  };

  const onSvgPointerUp = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!dragRef.current || !preview) return;
    const { px, py } = toPreviewCoords(e);
    const s = dragRef.current;
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    dragRef.current = null;
    setDragBox(null);
    if (Math.abs(px - s.px) < 3 || Math.abs(py - s.py) < 3) return; // 误触
    setPrompts((p) => [...p, boxFromDrag(s.px, s.py, px, py, preview.meta)]);
  };

  /** 指针取消（系统中断/移出边界释放）：清掉预览框，不留粘住的拖拽。 */
  const onSvgPointerCancel = (e: React.PointerEvent<SVGSVGElement>) => {
    e.currentTarget.releasePointerCapture?.(e.pointerId);
    cancelDrag();
  };

  const onOverlayKeyDown = (e: React.KeyboardEvent<SVGSVGElement>) => {
    if (!preview) return;
    const stepX = Math.max(1, Math.round(preview.meta.preview_width / 50));
    const stepY = Math.max(1, Math.round(preview.meta.preview_height / 50));
    const center = { px: preview.meta.preview_width / 2, py: preview.meta.preview_height / 2 };
    const moveCursor = (dx: number, dy: number) => {
      e.preventDefault();
      setKbdCursor((c) => {
        const next = c ?? center;
        return {
          px: clamp(next.px + dx, 0, preview.meta.preview_width),
          py: clamp(next.py + dy, 0, preview.meta.preview_height),
        };
      });
    };
    switch (e.key) {
      case 'ArrowLeft': moveCursor(-stepX, 0); return;
      case 'ArrowRight': moveCursor(stepX, 0); return;
      case 'ArrowUp': moveCursor(0, -stepY); return;
      case 'ArrowDown': moveCursor(0, stepY); return;
      case 'b':
      case 'B':
        // 键盘等价于触屏的模式按钮：不依赖物理修饰键（#2159）。
        e.preventDefault();
        setPromptMode((m) => (m === 'point' ? 'box' : 'point'));
        setKbdBoxStart(null);
        return;
      case 'Escape':
        e.preventDefault();
        cancelDrag();
        return;
      case 'Enter':
      case ' ': {
        e.preventDefault();
        const c = kbdCursor ?? center;
        if (promptMode === 'point') {
          addPointAt(c.px, c.py);
        } else if (!kbdBoxStart) {
          setKbdBoxStart(c);
        } else {
          setKbdBoxStart(null);
          // 与指针路径同款的误触保护：近乎零面积的框不提交。
          if (Math.abs(c.px - kbdBoxStart.px) >= 3 && Math.abs(c.py - kbdBoxStart.py) >= 3) {
            setPrompts((p) => [...p, boxFromDrag(kbdBoxStart.px, kbdBoxStart.py, c.px, c.py, preview.meta)]);
          }
        }
        return;
      }
      default:
    }
  };

  const promptMarks = useMemo(() => {
    if (!preview) return [];
    return prompts.map((p) => {
      if (p.kind === 'point') {
        const { px, py } = mapToScreen(p.x, p.y, preview.meta);
        return { kind: 'point' as const, px, py };
      }
      const a = mapToScreen(p.x, p.y + p.h, preview.meta);
      const b = mapToScreen(p.x + p.w, p.y, preview.meta);
      return {
        kind: 'box' as const,
        px: Math.min(a.px, b.px),
        py: Math.min(a.py, b.py),
        w: Math.abs(b.px - a.px),
        h: Math.abs(b.py - a.py),
      };
    });
  }, [preview, prompts]);

  const candidatePolygons = useMemo(() => {
    if (!preview) return [];
    type Ring = [number, number][];
    const out: { candidate: number; points: string; color: string }[] = [];
    let rendered = 0;
    for (const f of candidateFeatures) {
      if (rendered >= 500) break; // 渲染上限（超大 run 不拖死 DOM）
      rendered += 1;
      const cand = Number(f.properties?.candidate ?? 0);
      if (f.geometry?.type !== 'Polygon') continue;
      const rings = f.geometry.coordinates as Ring[];
      const ring = rings?.[0] ?? [];
      const pts = ring
        .map(([x, y]) => {
          const { px, py } = mapToScreen(x, y, preview.meta);
          return `${px},${py}`;
        })
        .join(' ');
      if (pts) {
        out.push({
          candidate: cand,
          points: pts,
          color: CANDIDATE_COLORS[cand % CANDIDATE_COLORS.length],
        });
      }
    }
    return out;
  }, [candidateFeatures, preview]);

  // 实时播报文本（#2163）：加载/提交中、错误、候选与队列摘要。
  const liveMessage = busy
    ? t('statusSubmitting')
    : previewLoading
      ? t('statusPreviewLoading')
      : candidates.length > 0
        ? t('statusCandidates', { count: candidates.length })
        : '';

  return (
    <div
      data-testid="geoai-panel"
      // #2161：全局 body overflow:hidden，页面必须自持滚动区才能在
      // 320×568 上到达批次队列；safe-area 下内边距留给 Home 条。
      className="mx-auto flex h-dvh max-w-6xl flex-col gap-4 overflow-y-auto p-4 pb-[max(2rem,env(safe-area-inset-bottom))] sm:p-6"
    >
      <header className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold">{t('title')}</h1>
        <span className="text-xs text-ink-muted">Platform 11 · ADR-0198</span>
      </header>

      <section aria-label={t('controlsSection')} className="flex flex-col gap-2 sm:flex-row sm:flex-wrap sm:items-center">
        <label htmlFor="geoai-source-uri" className="sr-only">{t('sourceLabel')}</label>
        <input
          id="geoai-source-uri"
          data-testid="source-uri"
          aria-label={t('sourceLabel')}
          // #2161：w-full + min-w-0 —— 固定 min-w-72 在 320px 内容盒上溢出。
          className="w-full min-w-0 flex-1 rounded border border-edge bg-surface-sunken px-2 py-1.5 text-sm text-ink placeholder:text-ink-disabled"
          placeholder={t('sourcePlaceholder')}
          value={sourceUri}
          onChange={(e) => setSourceUri(e.target.value)}
        />
        <button
          data-testid="load-models"
          className="touch-target rounded border border-edge bg-surface-raised px-3 py-1.5 text-sm text-ink hover:bg-surface-hover disabled:opacity-50"
          disabled={modelsLoading}
          aria-busy={modelsLoading}
          onClick={loadModels}
        >
          {modelsLoading ? t('loading') : t('loadModels')}
        </button>
        <label htmlFor="geoai-model-select" className="sr-only">{t('modelLabel')}</label>
        <select
          id="geoai-model-select"
          data-testid="model-select"
          aria-label={t('modelLabel')}
          className="w-full min-w-0 rounded border border-edge bg-surface-sunken px-2 py-1.5 text-sm text-ink sm:w-auto"
          value={modelId}
          onChange={(e) => setModelId(e.target.value)}
        >
          {models.length === 0 && <option value="">{t('noModels')}</option>}
          {models.map((m) => (
            <option key={m.model_id} value={m.model_id}>
              {m.model_id}
            </option>
          ))}
        </select>
        <button
          data-testid="load-preview"
          className="touch-target rounded border border-edge bg-surface-raised px-3 py-1.5 text-sm text-ink hover:bg-surface-hover disabled:opacity-50"
          disabled={previewLoading}
          aria-busy={previewLoading}
          onClick={loadPreview}
        >
          {previewLoading ? t('loading') : t('loadPreview')}
        </button>
      </section>

      {error && (
        <p role="alert" data-testid="geoai-error" className="rounded border border-status-critical-border bg-status-critical-soft px-3 py-2 text-sm text-status-critical">
          {error}
        </p>
      )}
      <p aria-live="polite" className="sr-only" data-testid="geoai-live">
        {liveMessage}
      </p>

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="relative self-start overflow-hidden rounded border border-edge-subtle bg-surface-sunken" aria-busy={previewLoading}>
          {preview ? (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={preview.png}
                alt={t('previewAlt')}
                width={preview.meta.preview_width}
                height={preview.meta.preview_height}
                className="block h-auto w-full max-w-full select-none"
                draggable={false}
              />
              <svg
                data-testid="prompt-overlay"
                // #2159：可聚焦控件（Tab 到达）+ 键盘/触屏等价路径。
                role="application"
                tabIndex={0}
                aria-label={t('overlayAria')}
                aria-describedby="geoai-prompt-hint"
                className="absolute inset-0 cursor-crosshair touch-none outline-none"
                viewBox={`0 0 ${preview.meta.preview_width} ${preview.meta.preview_height}`}
                width="100%"
                height="100%"
                onPointerDown={onSvgPointerDown}
                onPointerMove={onSvgPointerMove}
                onPointerUp={onSvgPointerUp}
                onPointerCancel={onSvgPointerCancel}
                onKeyDown={onOverlayKeyDown}
                onFocus={() => setOverlayFocused(true)}
                onBlur={() => setOverlayFocused(false)}
              >
                {candidatePolygons.map((poly, i) => (
                  <polygon
                    key={`cand-${i}`}
                    data-testid={`candidate-poly-${poly.candidate}`}
                    points={poly.points}
                    fill={poly.color}
                    fillOpacity={0.25}
                    stroke={poly.color}
                    strokeWidth={1.5}
                  />
                ))}
                {promptMarks.map((m, i) =>
                  m.kind === 'point' ? (
                    <circle
                      key={`pt-${i}`}
                      data-testid={`prompt-point-${i}`}
                      cx={m.px}
                      cy={m.py}
                      r={4}
                      fill="#ef4444"
                      stroke="#fff"
                      strokeWidth={1}
                    />
                  ) : (
                    <rect
                      key={`bx-${i}`}
                      data-testid={`prompt-box-${i}`}
                      x={m.px}
                      y={m.py}
                      width={m.w}
                      height={m.h}
                      fill="rgba(239,68,68,0.12)"
                      stroke="#ef4444"
                      strokeWidth={1.5}
                    />
                  ),
                )}
                {dragBox && (
                  <rect
                    data-testid="drag-box"
                    x={dragBox.x}
                    y={dragBox.y}
                    width={dragBox.w}
                    height={dragBox.h}
                    fill="rgba(239,68,68,0.08)"
                    stroke="#ef4444"
                    strokeDasharray="4 2"
                  />
                )}
                {kbdBoxStart && overlayFocused && (
                  <rect
                    data-testid="kbd-box-start"
                    x={kbdBoxStart.px}
                    y={kbdBoxStart.py}
                    width={(kbdCursor?.px ?? kbdBoxStart.px) - kbdBoxStart.px}
                    height={(kbdCursor?.py ?? kbdBoxStart.py) - kbdBoxStart.py}
                    fill="rgba(239,68,68,0.08)"
                    stroke="#ef4444"
                    strokeDasharray="4 2"
                  />
                )}
                {kbdCursor && overlayFocused && (
                  <g data-testid="kbd-cursor">
                    <circle cx={kbdCursor.px} cy={kbdCursor.py} r={5} fill="none" stroke="#ef4444" strokeWidth={1.5} />
                    <line x1={kbdCursor.px - 9} x2={kbdCursor.px + 9} y1={kbdCursor.py} y2={kbdCursor.py} stroke="#ef4444" strokeWidth={1} />
                    <line x1={kbdCursor.px} x2={kbdCursor.px} y1={kbdCursor.py - 9} y2={kbdCursor.py + 9} stroke="#ef4444" strokeWidth={1} />
                  </g>
                )}
              </svg>
              <div id="geoai-prompt-hint" className="flex flex-wrap items-center gap-2 border-t border-edge-subtle bg-surface-panel px-2 py-1.5 text-xs text-ink-secondary">
                <span>{t('keyboardHint')}</span>
                <button
                  type="button"
                  data-testid="mode-point"
                  aria-pressed={promptMode === 'point'}
                  className={`touch-target rounded border px-2 py-0.5 ${
                    promptMode === 'point'
                      ? 'border-status-info-border bg-status-info-soft text-status-info'
                      : 'border-edge bg-surface-raised text-ink'
                  }`}
                  onClick={() => { setPromptMode('point'); setKbdBoxStart(null); }}
                >
                  {t('promptModePoint')}
                </button>
                <button
                  type="button"
                  data-testid="mode-box"
                  aria-pressed={promptMode === 'box'}
                  className={`touch-target rounded border px-2 py-0.5 ${
                    promptMode === 'box'
                      ? 'border-status-info-border bg-status-info-soft text-status-info'
                      : 'border-edge bg-surface-raised text-ink'
                  }`}
                  onClick={() => setPromptMode('box')}
                >
                  {t('promptModeBox')}
                </button>
              </div>
            </>
          ) : (
            <div
              data-testid="preview-placeholder"
              className="flex h-64 items-center justify-center p-4 text-center text-sm text-ink-muted"
            >
              {t('previewHint')}
            </div>
          )}
        </div>

        <aside className="flex flex-col gap-3" aria-label={t('resultsSection')}>
          <div className="flex gap-2">
            <button
              data-testid="submit"
              className="touch-target flex-1 rounded bg-status-accent px-3 py-1.5 text-sm font-medium text-ink-on-accent disabled:opacity-50"
              disabled={busy || previewLoading || !preview || prompts.length === 0}
              aria-busy={busy}
              onClick={submit}
            >
              {busy ? t('statusSubmitting') : t('submitWithCount', { count: prompts.length })}
            </button>
            <button
              data-testid="undo"
              className="touch-target rounded border border-edge bg-surface-raised px-3 py-1.5 text-sm text-ink hover:bg-surface-hover"
              onClick={undo}
            >
              {t('undo')}
            </button>
          </div>

          <div className="rounded border border-edge-subtle p-2">
            <h2 className="mb-1 text-sm font-medium">{t('candidatesTitle')}</h2>
            {candidates.length === 0 ? (
              <p className="text-xs text-ink-muted">{t('candidatesEmpty')}</p>
            ) : (
              <ul className="flex flex-col gap-1" data-testid="candidate-list">
                {candidates.map((c) => (
                  <li key={c.index} className="flex items-center justify-between gap-2 text-sm">
                    <span className="flex items-center gap-1">
                      <span
                        className="inline-block h-2.5 w-2.5 rounded-full"
                        style={{ background: CANDIDATE_COLORS[c.index % CANDIDATE_COLORS.length] }}
                      />
                      {t('candidateLabel', { index: c.index, score: c.score.toFixed(3) })}
                      {c.source === 'heuristic' && (
                        <span className="text-[10px] text-ink-muted">{t('heuristicTag')}</span>
                      )}
                    </span>
                    <button
                      data-testid={`accept-${c.index}`}
                      className="touch-target rounded border border-edge bg-surface-raised px-2 py-1 text-xs text-ink hover:bg-surface-hover disabled:opacity-50"
                      disabled={busy}
                      onClick={() => acceptCandidate(c.index)}
                    >
                      {t('accept')}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </div>

          <div className="rounded border border-edge-subtle p-2">
            <h2 className="mb-1 text-sm font-medium">{t('queueTitle')}</h2>
            {queue.length === 0 ? (
              <p className="text-xs text-ink-muted">{t('queueEmpty')}</p>
            ) : (
              <ul data-testid="run-queue" className="flex flex-col gap-1 text-xs">
                {queue.map((item) => (
                  <li key={item.id} data-testid={`queue-${item.id}`} className="flex justify-between gap-2">
                    <span className="truncate">{item.label}</span>
                    <span
                      className={
                        item.status === 'done'
                          ? 'text-status-success'
                          : item.status === 'error'
                            ? 'text-status-critical'
                            : 'text-ink-muted'
                      }
                    >
                      {item.status === 'done' ? item.runId : item.status === 'error' ? (item.error ?? item.status) : t('queueStatusRunning')}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </aside>
      </div>
    </div>
  );
}
