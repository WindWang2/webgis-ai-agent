'use client';

/**
 * G34 专业纸面版式与图册编排工作台（WP09）。
 *
 * 单一真相 = committed MapSpec.layout.print_layout（后端 REST 读取，随
 * map-state 恢复；绝不建 localStorage 作者文档）。所有写经 /print-layout/apply
 * → 后端 service → apply_gis_mutation(SetLayoutIntent) 一次 CAS，与 Pi 工具
 * 同一服务/锁/撤销栈。
 *
 * 四态可见：committed / draft / font_unavailable / export_unavailable。
 * 页面切换递增 generation，旧缩略图/字体请求返回即丢弃。
 * 预览缩放只改屏幕比例，不改持久毫米坐标；编辑文本时快捷键不触发地图手势。
 */
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react';
import type { KeyboardEvent as ReactKeyboardEvent } from 'react';
import { useT } from '@/lib/i18n/useT';
import { useToastStore } from '@/components/ui/toast';
import { getMapSpecSessionCursor, setMapSpecRevision } from '@/lib/mapspec/session-cursor';
import {
  draftFieldErrors, defaultPrintLayout, isDraftDirty,
  initialPrintLayoutState, reducePrintLayout,
  type PrintComponent,
} from '@/lib/print-layout/contracts';
import { previewScale } from '@/lib/print-layout/units';
import {
  asPrintLayoutError, fetchCapabilities, fetchInspect, fetchPages,
  postApply, postPreview, postPropose, postExport, postTemplate,
  type Capabilities, type PagesResult, type PrintLayoutApiContext,
} from '@/lib/print-layout/api';

type Props = { onClose?: () => void };

function ctxOf(): PrintLayoutApiContext | null {
  const c = getMapSpecSessionCursor();
  if (!c.sessionId) return null;
  return { sessionId: c.sessionId, ownerToken: c.ownerToken };
}

/** 编辑控件：阻止键盘事件冒泡到地图手势（验收场景 WP09）。 */
function stopMapKeys(e: ReactKeyboardEvent) {
  e.stopPropagation();
}

export function PrintLayoutPanel({ onClose }: Props) {
  const t = useT();
  const [state, dispatch] = useReducer(reducePrintLayout, undefined, initialPrintLayoutState);
  const [caps, setCaps] = useState<Capabilities | null>(null);
  const [pages, setPages] = useState<PagesResult | null>(null);
  const [validation, setValidation] = useState<string[]>([]);
  const genRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const toast = useToastStore((s) => s.addToast);

  const reload = useCallback(async () => {
    const ctx = ctxOf();
    if (!ctx) {
      dispatch({ type: 'load-failed', generation: state.generation,
        error: 'no_session' });
      return;
    }
    const generation = genRef.current + 1;
    genRef.current = generation;
    // page-switch 把 reducer 的 generation 同步到同一个值，作废在途回包。
    dispatch({ type: 'page-switch', pageId: '' });
    dispatch({ type: 'load-start' });
    try {
      const [inspect, capabilities, pageList] = await Promise.all([
        fetchInspect({ ...ctx, signal: abortRef.current?.signal }, true),
        fetchCapabilities({ ...ctx, signal: abortRef.current?.signal }),
        fetchPages({ ...ctx, signal: abortRef.current?.signal }).catch(() => null),
      ]);
      if (generation !== genRef.current) return;
      setCaps(capabilities);
      setPages(pageList);
      dispatch({
        type: 'load-ok',
        generation,
        plan: inspect.plan ?? null,
        revision: inspect.mapspec_revision,
      });
    } catch (err) {
      if (generation !== genRef.current) return;
      dispatch({ type: 'load-failed', generation,
        error: asPrintLayoutError(err).code });
    }
  }, []);

  useEffect(() => {
    abortRef.current = new AbortController();
    void reload();
    return () => abortRef.current?.abort();
    // 只在挂载时加载一次；后续由显式刷新按钮触发。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const fieldErrors = useMemo(() => draftFieldErrors(state.draft), [state.draft]);
  const dirty = useMemo(
    () => isDraftDirty(state.committed, state.draft), [state.committed, state.draft]);
  const fontsUnavailable = caps?.fonts.every((f) => !f.available) ?? false;
  const exportUnavailable = state.phase === 'font_unavailable' || fontsUnavailable
    || !state.committed || fieldErrors.length > 0;

  const runPreview = useCallback(async () => {
    const ctx = ctxOf();
    if (!ctx || !state.draft) return;
    const generation = state.generation;
    dispatch({ type: 'preview-start' });
    try {
      const out = await postPreview(ctx, {
        page_id: state.pageId, dpi: state.dpi, include_svg: true,
      });
      if (generation !== genRef.current) return;  // 旧页回包丢弃
      if (!out.success || !out.svg) {
        dispatch({ type: 'preview-failed', generation: state.generation,
          error: out.code ?? 'preview_failed',
          fontUnavailable: out.code === 'FONT_RESOURCE_UNAVAILABLE' });
        return;
      }
      dispatch({ type: 'preview-ok', generation: state.generation,
        svg: out.svg, receipt: out.receipt ?? {},
        fingerprint: out.svg_sha256 ?? '' });
    } catch (err) {
      dispatch({ type: 'preview-failed', generation: state.generation,
        error: asPrintLayoutError(err).code });
    }
  }, [state.draft, state.dpi, state.generation, state.pageId]);

  const runApply = useCallback(async () => {
    const ctx = ctxOf();
    if (!ctx || !state.draft) return;
    if (fieldErrors.length > 0) {
      setValidation(fieldErrors);
      return;
    }
    // CAS 基线用 **inspect 回来的 state.revision**，不是全局 session cursor：
    // cursor 可能比本面板的草稿基线旧（误报 SUPERSEDED）或更新（覆盖他人写入）。
    const operations = [{ op: 'replace_plan', plan: state.draft }];
    dispatch({ type: 'apply-start' });
    try {
      const dry = await postPropose(ctx, {
        operations, initialize: !state.committed, dpi: state.dpi,
      }).catch(() => null);
      if (dry && !dry.success) {
        dispatch({ type: 'apply-failed', error: dry.code ?? 'propose_failed' });
        setValidation((dry.issues ?? []).map((e) => String(e.code)));
        return;
      }
      const out = await postApply(ctx, {
        operations,
        initialize: !state.committed,
        expected_revision: state.revision,
        dpi: state.dpi,
      });
      if (!out.success || !out.committed) {
        dispatch({ type: 'apply-failed', error: out.code ?? 'apply_failed' });
        setValidation((out.validation?.errors ?? []).map((e) => String(e.code)));
        return;
      }
      if (typeof out.mutation_revision === 'number') {
        setMapSpecRevision(out.mutation_revision);
      }
      dispatch({ type: 'apply-ok', plan: state.draft,
        revision: out.mutation_revision ?? state.revision });
      setValidation([]);
      toast(t('workbench.printLayout.applied'), 'success');
    } catch (err) {
      dispatch({ type: 'apply-failed', error: asPrintLayoutError(err).code });
    }
  }, [state.draft, state.committed, state.dpi, state.revision, fieldErrors, toast, t]);

  const runExport = useCallback(async (format: 'pdf' | 'svg' | 'png') => {
    const ctx = ctxOf();
    if (!ctx) return;
    dispatch({ type: 'export-start' });
    try {
      const out = await postExport(ctx, { format, dpi: state.dpi, page_id: state.pageId });
      if (!out.success) {
        dispatch({ type: 'export-failed', error: out.code ?? 'export_failed' });
        return;
      }
      dispatch({ type: 'export-ok', refs: {
        download_url: out.download_url, manifest_url: out.manifest_url, format,
      } });
      toast(t('workbench.printLayout.exported'), 'success');
    } catch (err) {
      dispatch({ type: 'export-failed', error: asPrintLayoutError(err).code });
    }
  }, [state.dpi, state.pageId, toast, t]);

  const patchComponent = useCallback((id: string, patch: Partial<PrintComponent>) => {
    dispatch({ type: 'patch-component', id, patch });
  }, []);

  const alignSelected = useCallback((edge: string) => {
    if (!state.draft || state.selectedIds.length < 2) return;
    // 对齐在后端 operations 上原子执行；前端先给出本地预览结果。
    const frames = state.draft.components
      .filter((c) => state.selectedIds.includes(c.id))
      .map((c) => ({ ...c.frame }));
    const target = edge === 'left' ? Math.min(...frames.map((f) => f.x))
      : edge === 'top' ? Math.min(...frames.map((f) => f.y))
        : edge === 'center-x'
          ? frames.reduce((a, f) => a + f.x + f.width / 2, 0) / frames.length
          : edge === 'center-y'
            ? frames.reduce((a, f) => a + f.y + f.height / 2, 0) / frames.length
            : 0;
    const draft = structuredClone(state.draft);
    for (const c of draft.components) {
      if (!state.selectedIds.includes(c.id) || c.locked) continue;
      if (edge === 'left') c.frame.x = target;
      else if (edge === 'top') c.frame.y = target;
      else if (edge === 'center-x') c.frame.x = target - c.frame.width / 2;
      else if (edge === 'center-y') c.frame.y = target - c.frame.height / 2;
    }
    dispatch({ type: 'patch-plan', patch: { components: draft.components } });
  }, [state.draft, state.selectedIds]);

  const initializePlan = useCallback(() => {
    dispatch({ type: 'patch-plan', patch: defaultPrintLayout() });
    dispatch({ type: 'select', ids: [] });
  }, []);

  const saveTemplate = useCallback(async () => {
    const ctx = ctxOf();
    if (!ctx || !state.draft) return;
    try {
      await postTemplate(ctx, {
        action: 'save', template_id: `tpl:g34-${Date.now().toString(36)}`,
        name: t('workbench.printLayout.templateDefaultName'),
        paper_profile: state.draft.paper.profile,
        components: state.draft.components,
        expected_revision: state.revision,
        initialize: false,
      });
      toast(t('workbench.printLayout.templateSaved'), 'success');
    } catch (err) {
      toast(asPrintLayoutError(err).code, 'error');
    }
  }, [state.draft, state.revision, toast, t]);

  const resetOverrides = useCallback(async () => {
    const ctx = ctxOf();
    if (!ctx) return;
    try {
      await postTemplate(ctx, {
        action: 'reset', page_id: state.pageId,
        expected_revision: state.revision, initialize: false,
      });
      toast(t('workbench.printLayout.resetDone'), 'success');
      void reload();
    } catch (err) {
      toast(asPrintLayoutError(err).code, 'error');
    }
  }, [state.pageId, state.revision, toast, t, reload]);

  const statusLabel = useMemo(() => {
    if (state.phase === 'font_unavailable') return 'font_unavailable';
    if (state.phase === 'cancelled') return 'cancelled';
    if (state.phase === 'blocked' || state.phase === 'failed') return 'blocked';
    if (state.phase === 'exporting') return 'exporting';
    if (state.phase === 'draft') return 'draft';
    if (state.phase === 'committed') return 'committed';
    return 'idle';
  }, [state.phase]);

  const paper = state.draft?.paper ?? { width_mm: 297, height_mm: 210 };
  const scale = previewScale(state.zoom);

  return (
    <div className="flex h-full min-h-0 flex-col gap-3 p-3 text-body"
         data-testid="print-layout-panel">
      <header className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-subtitle font-semibold text-ink">
            {t('workbench.printLayout.title')}
          </h2>
          <p className="text-meta text-ink-muted">
            {t('workbench.printLayout.subtitle')}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className="rounded border border-edge-subtle px-2 py-0.5 text-meta"
                data-testid="print-layout-status" data-status={statusLabel}>
            {t(`workbench.printLayout.status.${statusLabel}`)}
          </span>
          {onClose && (
            <button type="button" className="btn btn-ghost text-meta" onClick={onClose}>
              {t('common.close')}
            </button>
          )}
        </div>
      </header>

      {/* 纸张 / 模板 / DPI / 缩放 */}
      <section className="flex flex-wrap items-center gap-2 rounded-md border border-edge-subtle bg-surface-sunken p-2"
               aria-label={t('workbench.printLayout.paperSection')}>
        <label className="flex items-center gap-1 text-meta">
          {t('workbench.printLayout.paper')}
          <select className="input input-sm" value={state.draft?.paper.profile ?? 'a4_landscape'}
                  onKeyDown={stopMapKeys}
                  onChange={(e) => {
                    const capsEntry = caps?.paper_profiles[e.target.value];
                    if (!capsEntry) return;
                    dispatch({ type: 'patch-plan', patch: {
                      paper: {
                        profile: e.target.value,
                        width_mm: capsEntry.width_mm,
                        height_mm: capsEntry.height_mm,
                        orientation: capsEntry.height_mm > capsEntry.width_mm
                          ? 'portrait' : 'landscape',
                      },
                    } });
                  }}>
            {Object.keys(caps?.paper_profiles ?? { a4_landscape: { width_mm: 297, height_mm: 210 } })
              .map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1 text-meta">
          DPI
          <select className="input input-sm" value={state.dpi}
                  onKeyDown={stopMapKeys}
                  onChange={(e) => dispatch({ type: 'dpi', dpi: Number(e.target.value) })}>
            {[96, 150, 300].map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-1 text-meta">
          {t('workbench.printLayout.zoom')}
          <input className="input input-sm w-16" type="number" min={0.25} max={4}
                 step={0.25} value={state.zoom} onKeyDown={stopMapKeys}
                 onChange={(e) => dispatch({ type: 'zoom', zoom: Number(e.target.value) })} />
        </label>
        <button type="button" className="btn btn-ghost text-meta" onClick={saveTemplate}
                disabled={!state.draft || state.busy}>
          {t('workbench.printLayout.saveTemplate')}
        </button>
        <button type="button" className="btn btn-ghost text-meta" onClick={resetOverrides}
                disabled={state.busy}>
          {t('workbench.printLayout.resetOverrides')}
        </button>
      </section>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[260px_1fr_280px]">
        {/* 页清单 */}
        <section className="min-h-0 overflow-auto rounded-md border border-edge-subtle p-2"
                 aria-label={t('workbench.printLayout.pagesSection')}>
          <h3 className="mb-2 text-meta font-semibold text-ink">
            {t('workbench.printLayout.pageList')} · {pages?.page_count ?? 1}/{pages?.page_budget ?? 20}
          </h3>
          {(pages?.pages ?? [{ page_id: 'main', title: '', page_number: 1 }]).map((p) => {
            const pid = String(p.page_id ?? 'main');
            return (
              <button key={pid} type="button"
                      className={`mb-1 w-full rounded border px-2 py-1 text-left text-meta ${
                        state.pageId === pid ? 'border-accent bg-surface-raised' : 'border-edge-subtle'
                      }`}
                      data-testid={`print-layout-page-${pid}`}
                      onClick={() => {
                        dispatch({ type: 'page-switch', pageId: pid });
                        genRef.current += 1;
                      }}>
                {String(p.page_number ?? 1)}. {String(p.title ?? pid) || pid}
                <span className="ml-1 text-ink-muted">({pid})</span>
              </button>
            );
          })}
          {pages?.exceeds_budget && (
            <p className="mt-2 text-meta text-danger">
              {t('workbench.printLayout.pageBudgetExceeded')}
            </p>
          )}
        </section>

        {/* 可缩放纸面预览：纸边 / 安全区 / 组件框 / 真实 SVG */}
        <section className="min-h-0 overflow-auto rounded-md border border-edge-subtle bg-surface-sunken p-2"
                 aria-label={t('workbench.printLayout.previewSection')}>
          <div className="mb-2 flex items-center justify-between text-meta">
            <span>{paper.width_mm} × {paper.height_mm} mm</span>
            <span>{t('workbench.printLayout.previewHint')}</span>
          </div>
          <div className="relative mx-auto border border-edge bg-white shadow-sm"
               data-testid="print-layout-paper"
               style={{
                 width: paper.width_mm * scale, height: paper.height_mm * scale,
                 minWidth: 80, minHeight: 60,
               }}>
            {/* 安全区（只显示，不影响持久坐标） */}
            {state.draft && (
              <div className="pointer-events-none absolute border border-dashed border-amber-400/70"
                   style={{
                     left: state.draft.safe_area.left * scale,
                     top: state.draft.safe_area.top * scale,
                     right: state.draft.safe_area.right * scale,
                     bottom: state.draft.safe_area.bottom * scale,
                   }} />
            )}
            {/* 真实 SVG（同一作者决定，来自生产编译器） */}
            {state.previewSvg && (
              <div className="absolute inset-0 [&>svg]:h-full [&>svg]:w-full"
                   data-testid="print-layout-svg"
                   data-fingerprint={state.previewFingerprint ?? ''}
                   // SVG 由后端生产编译器生成并转义；不含用户注入脚本。
                   dangerouslySetInnerHTML={{ __html: state.previewSvg }} />
            )}
            {/* 可编辑组件框（键盘可选、数值可改） */}
            {state.draft?.components.map((c) => (
              <button key={c.id} type="button"
                      className={`absolute border text-[9px] leading-none ${
                        state.selectedIds.includes(c.id)
                          ? 'border-accent bg-accent/10 text-ink'
                          : 'border-sky-400/60 bg-sky-400/5 text-ink-muted'
                      } ${c.locked ? 'cursor-not-allowed opacity-70' : ''}`}
                      data-testid={`print-layout-component-${c.id}`}
                      data-locked={c.locked ? 'true' : 'false'}
                      style={{
                        left: c.frame.x * scale, top: c.frame.y * scale,
                        width: c.frame.width * scale, height: c.frame.height * scale,
                      }}
                      onKeyDown={stopMapKeys}
                      onClick={(e) => {
                        const ids = e.shiftKey
                          ? (state.selectedIds.includes(c.id)
                            ? state.selectedIds.filter((i) => i !== c.id)
                            : [...state.selectedIds, c.id])
                          : [c.id];
                        dispatch({ type: 'select', ids });
                      }}>
                {c.id}{c.locked ? ' 🔒' : ''}
              </button>
            ))}
          </div>
        </section>

        {/* 组件表 + 数值属性 + 验证提示 */}
        <section className="min-h-0 overflow-auto rounded-md border border-edge-subtle p-2"
                 aria-label={t('workbench.printLayout.componentsSection')}>
          <h3 className="mb-2 text-meta font-semibold text-ink">
            {t('workbench.printLayout.components')} · {state.draft?.components.length ?? 0}/32
          </h3>
          {!state.draft && (
            <button type="button" className="btn btn-primary w-full text-meta"
                    onClick={initializePlan}>
              {t('workbench.printLayout.initialize')}
            </button>
          )}
          {state.draft?.components.map((c) => (
            <div key={c.id} className="mb-2 rounded border border-edge-subtle p-1.5 text-meta">
              <div className="mb-1 flex items-center justify-between">
                <button type="button" className="font-medium text-ink"
                        onClick={() => dispatch({ type: 'select', ids: [c.id] })}>
                  {c.id} <span className="text-ink-muted">({c.kind})</span>
                </button>
                {c.locked && <span className="text-meta">🔒 {t('workbench.printLayout.locked')}</span>}
              </div>
              <div className="grid grid-cols-4 gap-1">
                {(['x', 'y', 'width', 'height'] as const).map((k) => (
                  <label key={k} className="flex flex-col text-[10px] text-ink-muted">
                    {k} (mm)
                    <input className="input input-sm w-full px-1 py-0.5 text-[11px]"
                           type="number" step={0.5} disabled={c.locked}
                           value={c.frame[k]} onKeyDown={stopMapKeys}
                           aria-label={`${c.id}-${k}`}
                           onChange={(e) => patchComponent(c.id, {
                             frame: { ...c.frame, [k]: Number(e.target.value) },
                           })} />
                  </label>
                ))}
              </div>
              {c.typography && (
                <div className="mt-1 grid grid-cols-2 gap-1">
                  <label className="flex flex-col text-[10px] text-ink-muted">
                    {t('workbench.printLayout.font')}
                    <select className="input input-sm px-1 py-0.5 text-[11px]"
                            value={c.typography.font_id} onKeyDown={stopMapKeys}
                            onChange={(e) => patchComponent(c.id, {
                              typography: { ...c.typography!, font_id: e.target.value as never },
                            })}>
                      {(caps?.fonts ?? []).map((f) => (
                        <option key={f.font_id} value={f.font_id} disabled={!f.available}>
                          {f.family}{f.available ? '' : ' (N/A)'}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className="flex flex-col text-[10px] text-ink-muted">
                    {t('workbench.printLayout.fontSize')} (pt)
                    <input className="input input-sm px-1 py-0.5 text-[11px]"
                           type="number" min={4} max={96} step={0.5}
                           value={c.typography.size_pt} onKeyDown={stopMapKeys}
                           onChange={(e) => patchComponent(c.id, {
                             typography: { ...c.typography!, size_pt: Number(e.target.value) },
                           })} />
                  </label>
                </div>
              )}
              {c.legend && (
                <label className="mt-1 flex items-center gap-1 text-[10px] text-ink-muted">
                  {t('workbench.printLayout.legendColumns')}
                  <input className="input input-sm w-14 px-1 py-0.5 text-[11px]"
                         type="number" min={1} max={4} value={c.legend.columns}
                         onKeyDown={stopMapKeys}
                         onChange={(e) => patchComponent(c.id, {
                           legend: { ...c.legend!, columns: Number(e.target.value) },
                         })} />
                </label>
              )}
            </div>
          ))}
          <div className="mt-2 flex flex-wrap gap-1">
            {(['left', 'top', 'center-x', 'center-y'] as const).map((edge) => (
              <button key={edge} type="button" className="btn btn-ghost px-1.5 py-0.5 text-[10px]"
                      disabled={state.selectedIds.length < 2}
                      onClick={() => alignSelected(edge)}>
                {t(`workbench.printLayout.align.${edge}`)}
              </button>
            ))}
          </div>
          {(fieldErrors.length > 0 || validation.length > 0 || state.errors.length > 0) && (
            <ul className="mt-2 rounded border border-danger/40 bg-danger/5 p-1.5 text-[10px] text-danger"
                data-testid="print-layout-validation">
              {[...fieldErrors, ...validation, ...state.errors].slice(0, 8).map((e, i) => (
                <li key={`${e}-${i}`}>{e}</li>
              ))}
            </ul>
          )}
        </section>
      </div>

      <footer className="flex flex-wrap items-center gap-2 border-t border-edge-subtle pt-2">
        <button type="button" className="btn btn-ghost text-meta" onClick={() => void reload()}>
          {t('workbench.printLayout.reload')}
        </button>
        <button type="button" className="btn btn-ghost text-meta"
                disabled={!dirty} onClick={() => dispatch({ type: 'restore-committed' })}>
          {t('workbench.printLayout.cancel')}
        </button>
        <button type="button" className="btn btn-ghost text-meta"
                disabled={!state.draft || state.busy} onClick={() => void runPreview()}>
          {t('workbench.printLayout.preview')}
        </button>
        <button type="button" className="btn btn-primary text-meta"
                disabled={!dirty || state.busy || fieldErrors.length > 0}
                onClick={() => void runApply()}>
          {t('workbench.printLayout.apply')}
        </button>
        <button type="button" className="btn btn-ghost text-meta" onClick={() => {
          abortRef.current?.abort();
          abortRef.current = new AbortController();
          dispatch({ type: 'cancel' });
        }}>
          {t('workbench.printLayout.cancelOperation')}
        </button>
        <span className="flex-1" />
        {(['pdf', 'svg', 'png'] as const).map((fmt) => (
          <button key={fmt} type="button" className="btn btn-ghost text-meta"
                  disabled={exportUnavailable || state.busy}
                  data-testid={`print-layout-export-${fmt}`}
                  onClick={() => void runExport(fmt)}>
            {fmt.toUpperCase()}
          </button>
        ))}
        {state.exportRefs?.download_url && (
          <a className="text-meta text-accent underline"
             href={state.exportRefs.download_url} target="_blank" rel="noreferrer">
            {t('workbench.printLayout.download')}
          </a>
        )}
      </footer>
    </div>
  );
}
