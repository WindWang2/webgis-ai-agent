'use client';
/**
 * GeoAI 可提示分割面板（Platform 11 / WP-G；ADR-0198）。
 *
 * 自包含面板：不 import 既有 map 组件（并行 track 所有权），预览经
 * GET /geoai/preview（PNG base64 + 地理元数据），提示绘制在 SVG 覆盖层
 * （点击 = 点提示；拖拽 = 框提示），提交 = GeoPrompt artifact（CRS 来自
 * 预览元数据）；候选掩膜按分数列出，接受 = POST /geoai/prompt-refine；
 * 撤销 = 弹出提示；批次队列 = 提交历史状态机（idle/running/done/error）。
 */
import { useCallback, useMemo, useRef, useState } from 'react';

import { API_BASE } from '@/lib/api/config';
import { useT } from '@/lib/i18n/useT';

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

async function fetchJson(url: string, init?: RequestInit): Promise<unknown> {
  const resp = await fetch(url, init);
  const text = await resp.text();
  const body: unknown = text ? JSON.parse(text) : null;
  if (!resp.ok) {
    const detail =
      body && typeof body === 'object' && 'detail' in body
        ? String((body as { detail: unknown }).detail)
        : `${resp.status} ${resp.statusText}`;
    throw new Error(detail);
  }
  return body;
}

export function GeoAiPanel() {
  const t = useT('geoai');
  const [sourceUri, setSourceUri] = useState('');
  const [models, setModels] = useState<{ model_id: string }[]>([]);
  const [modelId, setModelId] = useState('');
  const [preview, setPreview] = useState<
    { png: string; meta: PreviewMeta } | null
  >(null);
  const [prompts, setPrompts] = useState<MapPrompt[]>([]);
  const [candidates, setCandidates] = useState<CandidateSummary[]>([]);
  const [candidateFeatures, setCandidateFeatures] = useState<GeoJsonFeature[]>([]);
  const [selectedRun, setSelectedRun] = useState<{
    runId: string;
    candidatesPath?: string;
  } | null>(null);
  const [queue, setQueue] = useState<QueueItem[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const dragRef = useRef<{ px: number; py: number } | null>(null);
  const [dragBox, setDragBox] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const seqRef = useRef(0);

  const loadModels = useCallback(async () => {
    try {
      const body = (await fetchJson(
        `${API_BASE}/api/v1/geoai/models?session_id=geoai-panel`,
      )) as { models: { model_id: string; task_types: string[] }[] };
      const promptable = body.models.filter((m) =>
        m.task_types.includes('promptable_segmentation'),
      );
      setModels(promptable);
      if (promptable.length > 0 && !modelId) setModelId(promptable[0].model_id);
    } catch (exc) {
      setError(t('errors.modelsLoadFailed', { err: String(exc) }));
    }
  }, [modelId, t]);

  const loadPreview = useCallback(async () => {
    setError('');
    setPreview(null);
    setCandidates([]);
    setCandidateFeatures([]);
    setSelectedRun(null);
    if (!sourceUri.trim()) {
      setError(t('errors.sourceRequired'));
      return;
    }
    try {
      const body = (await fetchJson(
        `${API_BASE}/api/v1/geoai/preview?source_uri=${encodeURIComponent(sourceUri.trim())}`,
      )) as { png_base64: string } & PreviewMeta;
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
      setError(t('errors.previewLoadFailed', { err: String(exc) }));
    }
  }, [sourceUri, t]);

  const submit = useCallback(async () => {
    if (!preview || prompts.length === 0 || !modelId) {
      setError(t('errors.submitPrecondition'));
      return;
    }
    setBusy(true);
    setError('');
    const id = ++seqRef.current;
    const label = `${modelId} · ${prompts.length}`;
    setQueue((q) => [...q, { id, label, status: 'running' as const }].slice(-20));
    try {
      const body = (await fetchJson(`${API_BASE}/api/v1/geoai/prompt-segment`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          model_id: modelId,
          source_uri: sourceUri.trim(),
          artifact: buildArtifactPayload(prompts, preview.meta),
          return_candidates: true,
          session_id: 'geoai-panel',
        }),
      })) as {
        run_id: string;
        outputs: {
          prompt_candidates?: {
            path?: string;
            windows?: { candidates: { index: number; score: number; source: string }[] }[];
          };
        };
      };
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
        candidatesPath: body.outputs.prompt_candidates?.path,
      });
      if (body.outputs.prompt_candidates?.path) {
        try {
          const geo = (await fetchJson(
            `${API_BASE}/api/v1/geoai/artifact-geojson?path=${encodeURIComponent(
              body.outputs.prompt_candidates.path,
            )}`,
          )) as { features: GeoJsonFeature[] };
          setCandidateFeatures(geo.features ?? []);
        } catch {
          setCandidateFeatures([]);
        }
      } else {
        setCandidateFeatures([]);
      }
      setQueue((q) =>
        q.map((item) =>
          item.id === id ? { ...item, status: 'done' as const, runId: body.run_id } : item,
        ),
      );
    } catch (exc) {
      setQueue((q) =>
        q.map((item) =>
          item.id === id ? { ...item, status: 'error' as const, error: String(exc) } : item,
        ),
      );
      setError(t('errors.submitFailed', { err: String(exc) }));
    } finally {
      setBusy(false);
    }
  }, [modelId, preview, prompts, sourceUri, t]);

  const acceptCandidate = useCallback(
    async (index: number) => {
      if (!selectedRun?.candidatesPath) {
        setError(t('errors.refinePrecondition'));
        return;
      }
      setBusy(true);
      try {
        const body = (await fetchJson(`${API_BASE}/api/v1/geoai/prompt-refine`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            model_id: modelId,
            source_uri: sourceUri.trim(),
            candidates_path: selectedRun.candidatesPath,
            candidate: index,
            session_id: 'geoai-panel',
          }),
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
        setError(t('errors.refineFailed', { err: String(exc) }));
      } finally {
        setBusy(false);
      }
    },
    [modelId, selectedRun, sourceUri, t],
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

  const onSvgPointerDown = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!preview) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    if (e.shiftKey) {
      dragRef.current = { px, py };
      setDragBox({ x: px, y: py, w: 0, h: 0 });
    } else {
      const pt = screenToMap(px, py, preview.meta);
      setPrompts((p) => [...p, { kind: 'point', x: pt.x, y: pt.y }]);
    }
  };

  const onSvgPointerMove = (e: React.PointerEvent<SVGSVGElement>) => {
    if (!dragRef.current || !preview) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
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
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const py = e.clientY - rect.top;
    const s = dragRef.current;
    dragRef.current = null;
    setDragBox(null);
    if (Math.abs(px - s.px) < 3 || Math.abs(py - s.py) < 3) return; // 误触
    setPrompts((p) => [...p, boxFromDrag(s.px, s.py, px, py, preview.meta)]);
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

  return (
    <div className="mx-auto flex max-w-6xl flex-col gap-4 p-6" data-testid="geoai-panel">
      <header className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold">{t('title')}</h1>
        <span className="text-xs text-ink-muted">Platform 11 · ADR-0198</span>
      </header>

      <section className="flex flex-wrap items-center gap-2">
        <input
          data-testid="source-uri"
          className="min-w-72 flex-1 rounded border px-2 py-1 text-sm"
          placeholder={t('sourcePlaceholder')}
          value={sourceUri}
          onChange={(e) => setSourceUri(e.target.value)}
        />
        <button
          data-testid="load-models"
          className="rounded bg-slate-200 px-3 py-1 text-sm"
          onClick={loadModels}
        >
          {t('loadModels')}
        </button>
        <select
          data-testid="model-select"
          className="rounded border px-2 py-1 text-sm"
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
          className="rounded bg-slate-200 px-3 py-1 text-sm"
          onClick={loadPreview}
        >
          {t('loadPreview')}
        </button>
      </section>

      {error && (
        <p data-testid="geoai-error" className="rounded bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </p>
      )}

      <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div className="relative overflow-hidden rounded border bg-slate-100">
          {preview ? (
            <>
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={preview.png}
                alt={t('previewAlt')}
                width={preview.meta.preview_width}
                height={preview.meta.preview_height}
                className="block max-w-full select-none"
                draggable={false}
              />
              <svg
                data-testid="prompt-overlay"
                className="absolute inset-0 cursor-crosshair"
                viewBox={`0 0 ${preview.meta.preview_width} ${preview.meta.preview_height}`}
                width={preview.meta.preview_width}
                height={preview.meta.preview_height}
                onPointerDown={onSvgPointerDown}
                onPointerMove={onSvgPointerMove}
                onPointerUp={onSvgPointerUp}
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
              </svg>
            </>
          ) : (
            <div
              data-testid="preview-placeholder"
              className="flex h-64 items-center justify-center text-sm text-ink-muted"
            >
              {t('previewHint')}
            </div>
          )}
        </div>

        <aside className="flex flex-col gap-3">
          <div className="flex gap-2">
            <button
              data-testid="submit"
              className="flex-1 rounded bg-emerald-600 px-3 py-1.5 text-sm text-white disabled:opacity-50"
              disabled={busy || !preview || prompts.length === 0}
              onClick={submit}
            >
              {t('submitWithCount', { count: prompts.length })}
            </button>
            <button
              data-testid="undo"
              className="rounded bg-slate-200 px-3 py-1.5 text-sm"
              onClick={undo}
            >
              {t('undo')}
            </button>
          </div>

          <div className="rounded border p-2">
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
                      className="rounded bg-slate-200 px-2 py-0.5 text-xs"
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

          <div className="rounded border p-2">
            <h2 className="mb-1 text-sm font-medium">{t('queueTitle')}</h2>
            {queue.length === 0 ? (
              <p className="text-xs text-ink-muted">{t('queueEmpty')}</p>
            ) : (
              <ul data-testid="run-queue" className="flex flex-col gap-1 text-xs">
                {queue.map((item) => (
                  <li key={item.id} data-testid={`queue-${item.id}`} className="flex justify-between">
                    <span className="truncate">{item.label}</span>
                    <span
                      className={
                        item.status === 'done'
                          ? 'text-emerald-600'
                          : item.status === 'error'
                            ? 'text-red-600'
                            : 'text-ink-muted'
                      }
                    >
                      {item.status === 'done' ? item.runId : item.status}
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
