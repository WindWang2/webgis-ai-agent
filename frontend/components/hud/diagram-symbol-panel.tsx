'use client';

/**
 * GOAL-20261126-04 地理锚定统计图符面板（对齐 thematic-authoring-panel 模式）。
 *
 * 面向**已有** geojson 图层：kind（组成饼/环形、多序列条形、等角分段）→
 * 类别×字段行 → 尺寸政策 → 计划（零 mutation，展示布局会计与图例）→
 * 单笔提交（与 Pi 同一 diagram_symbol_authoring 服务、同一 CAS 链）。
 *
 * 纪律：业务校验全部在服务端；切层/迟到响应由 epoch 守卫丢弃；
 * 预览态与已提交态视觉区分；关闭面板不留半提交状态。
 */
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useT } from '@/lib/i18n/useT';
import { useToastStore } from '@/components/ui/toast';
import { getMapSpecSessionCursor, subscribeMapSpecLive } from '@/lib/mapspec/session-cursor';
import {
  applyDiagramSymbol, asDiagramError, inspectDiagramLayer, planDiagramSymbol,
  type DiagramApiContext, type DiagramInspectReceipt, type DiagramPlanReceipt,
} from '@/lib/diagram-symbols/api';

interface Props {
  layerId: string;
}

const inputCls =
  'w-full rounded-md border border-edge-subtle bg-surface px-2 py-1 text-body text-ink ' +
  'focus:outline-none focus:ring-2 focus:ring-status-accent';
const labelCls = 'block text-caption text-ink-muted mb-1';
const btnCls =
  'rounded-md border border-edge-subtle px-2.5 py-1.5 text-body text-ink ' +
  'hover:bg-surface-hover focus:outline-none focus:ring-2 focus:ring-status-accent ' +
  'disabled:opacity-50 disabled:cursor-not-allowed';

const KINDS = ['composition_pie', 'composition_donut', 'series_bars', 'segmented_proportional'] as const;
type DiagramKind = (typeof KINDS)[number];
const PALETTES = ['Set1', 'Set2', 'Dark2', 'Pastel1'];

interface CategoryRow {
  category: string;
  field: string;
}

export function DiagramSymbolPanel({ layerId }: Props) {
  const t = useT('hud');
  const toast = useToastStore((s) => s.addToast);
  // 订阅 revision 变化（提交后 cursor.revision 前进 → 重渲染取新 CAS 基线）
  useSyncExternalStore(subscribeMapSpecLive, () => getMapSpecSessionCursor().revision);

  const epochRef = useRef(0);
  const [inspect, setInspect] = useState<DiagramInspectReceipt | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // ── 作者草稿（面板本地；关闭即丢弃，不改原图） ──
  const [kind, setKind] = useState<DiagramKind>('composition_pie');
  const [rows, setRows] = useState<CategoryRow[]>([
    { category: '', field: '' }, { category: '', field: '' },
  ]);
  const [unit, setUnit] = useState('');
  const [palette, setPalette] = useState('Set1');
  const [missingPolicy, setMissingPolicy] = useState<'omit' | 'block'>('omit');
  const [layout, setLayout] = useState<'stacked' | 'grouped'>('stacked');
  const [rMax, setRMax] = useState('32');
  const [hMax, setHMax] = useState('40');
  const [barW, setBarW] = useState('8');
  const [totalAreaEncoding, setTotalAreaEncoding] = useState(false);

  const [plan, setPlan] = useState<DiagramPlanReceipt | null>(null);
  const [busy, setBusy] = useState(false);
  const [planError, setPlanError] = useState<{ code: string; message: string; field?: string; hint?: string } | null>(null);

  const reload = useCallback(async () => {
    const cursor = getMapSpecSessionCursor();
    if (!cursor.sessionId) {
      setLoadError('no-session');
      setLoading(false);
      return;
    }
    const epoch = ++epochRef.current;
    const ctx: DiagramApiContext = { sessionId: cursor.sessionId, ownerToken: cursor.ownerToken };
    setLoading(true);
    try {
      const r = await inspectDiagramLayer(ctx, layerId);
      if (epoch !== epochRef.current) return;
      setInspect(r);
      setLoadError(null);
    } catch (err) {
      if (epoch !== epochRef.current) return;
      const payload = asDiagramError(err);
      setLoadError(`${payload.code}: ${payload.message}`);
    } finally {
      if (epoch === epochRef.current) setLoading(false);
    }
  }, [layerId]);

  useEffect(() => { void reload(); }, [reload]);

  const fields = inspect?.available_fields ?? [];

  const buildBlock = useCallback((): Record<string, unknown> | null => {
    const usable = rows.filter((r) => r.category.trim() && r.field.trim());
    if (usable.length < 2) return null;
    const sizePolicy: Record<string, unknown> = {};
    if (kind === 'series_bars') {
      sizePolicy.h_max_px = Number(hMax) || 40;
      sizePolicy.bar_w_px = Number(barW) || 8;
    } else {
      sizePolicy.r_max_px = Number(rMax) || 32;
    }
    if (kind === 'composition_donut') sizePolicy.inner_radius_ratio = 0.55;
    if (kind === 'composition_pie' || kind === 'composition_donut') {
      sizePolicy.total_area_encoding = totalAreaEncoding;
      if (totalAreaEncoding) sizePolicy.r_min_px = 4;
    }
    const block: Record<string, unknown> = {
      schema_version: 'diagram_symbol.v1',
      kind,
      categories: usable.map((r) => r.category.trim()),
      fields: usable.map((r) => r.field.trim()),
      missing_policy: missingPolicy,
      size_policy: sizePolicy,
      palette_id: palette,
    };
    if (kind === 'series_bars') block.layout = layout;
    if (unit.trim()) block.unit = unit.trim();
    return block;
  }, [rows, kind, unit, palette, missingPolicy, layout, rMax, hMax, barW, totalAreaEncoding]);

  const getCtx = useCallback((): DiagramApiContext | null => {
    const cursor = getMapSpecSessionCursor();
    if (!cursor.sessionId) return null;
    return { sessionId: cursor.sessionId, ownerToken: cursor.ownerToken };
  }, []);

  const runPlan = useCallback(async () => {
    const ctx = getCtx();
    const block = buildBlock();
    if (!ctx || !block) return;
    const epoch = epochRef.current;
    setBusy(true);
    setPlanError(null);
    try {
      const r = await planDiagramSymbol(ctx, layerId, block);
      if (epoch !== epochRef.current) return;
      setPlan(r);
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setPlan(null);
      setPlanError(asDiagramError(err));
    } finally {
      if (epoch === epochRef.current) setBusy(false);
    }
  }, [getCtx, buildBlock, layerId]);

  const runCommit = useCallback(async () => {
    const ctx = getCtx();
    const block = buildBlock();
    if (!ctx || !block) return;
    const cursor = getMapSpecSessionCursor();
    const epoch = epochRef.current;
    setBusy(true);
    try {
      await applyDiagramSymbol(
        ctx, layerId, block, 'commit', cursor.revision,
        `diagram-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
      if (epoch !== epochRef.current) return;
      toast(t('diagramSymbol.applied'), 'success');
      await reload();
    } catch (err) {
      if (epoch !== epochRef.current) return;
      const payload = asDiagramError(err);
      toast(`${payload.code}${payload.field ? ` @${payload.field}` : ''}: ${payload.message}`, 'error');
    } finally {
      if (epoch === epochRef.current) setBusy(false);
    }
  }, [getCtx, buildBlock, layerId, reload, t, toast]);

  const evidence = plan?.plan?.layout?.evidence as Record<string, number> | undefined;
  const legend = plan?.plan?.legend;
  const hasExisting = inspect?.has_existing_block ?? false;

  if (loading) {
    return <div className="p-3 text-caption text-ink-muted">{t('diagramSymbol.loading')}</div>;
  }
  if (loadError) {
    return <div className="p-3 text-caption text-status-danger">{loadError}</div>;
  }
  return (
    <div className="space-y-3 p-3">
      <div>
        <label className={labelCls}>{t('diagramSymbol.kind')}</label>
        <select className={inputCls} value={kind}
          onChange={(e) => setKind(e.target.value as DiagramKind)}>
          {KINDS.map((k) => (
            <option key={k} value={k}>{t(`diagramSymbol.kind_${k}`)}</option>
          ))}
        </select>
      </div>

      <div>
        <label className={labelCls}>{t('diagramSymbol.categories')}</label>
        <div className="space-y-1.5">
          {rows.map((row, i) => (
            <div key={i} className="flex items-center gap-1.5">
              <input className={inputCls} style={{ flex: 2 }}
                placeholder={t('diagramSymbol.categoryName')}
                value={row.category}
                onChange={(e) => setRows(rows.map((r, j) => j === i ? { ...r, category: e.target.value } : r))} />
              <select className={inputCls} style={{ flex: 3 }} value={row.field}
                onChange={(e) => setRows(rows.map((r, j) => j === i ? { ...r, field: e.target.value } : r))}>
                <option value="">{t('diagramSymbol.fieldPlaceholder')}</option>
                {fields.map((f) => <option key={f} value={f}>{f}</option>)}
              </select>
              <button className={btnCls} disabled={rows.length <= 2}
                onClick={() => setRows(rows.filter((_, j) => j !== i))}>−</button>
            </div>
          ))}
        </div>
        <button className={`${btnCls} mt-1`} disabled={rows.length >= 64}
          onClick={() => setRows([...rows, { category: '', field: '' }])}>
          + {t('diagramSymbol.addCategory')}
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className={labelCls}>{t('diagramSymbol.unit')}</label>
          <input className={inputCls} value={unit} onChange={(e) => setUnit(e.target.value)} />
        </div>
        <div>
          <label className={labelCls}>{t('diagramSymbol.palette')}</label>
          <select className={inputCls} value={palette} onChange={(e) => setPalette(e.target.value)}>
            {PALETTES.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
      </div>

      {kind === 'series_bars' ? (
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className={labelCls}>{t('diagramSymbol.barLayout')}</label>
            <select className={inputCls} value={layout} onChange={(e) => setLayout(e.target.value as 'stacked' | 'grouped')}>
              <option value="stacked">{t('diagramSymbol.layout_stacked')}</option>
              <option value="grouped">{t('diagramSymbol.layout_grouped')}</option>
            </select>
          </div>
          <div>
            <label className={labelCls}>{t('diagramSymbol.hMaxPx')}</label>
            <input className={inputCls} value={hMax} onChange={(e) => setHMax(e.target.value)} />
          </div>
          <div>
            <label className={labelCls}>{t('diagramSymbol.barW')}</label>
            <input className={inputCls} value={barW} onChange={(e) => setBarW(e.target.value)} />
          </div>
        </div>
      ) : (
        <div>
          <label className={labelCls}>{t('diagramSymbol.rMaxPx')}</label>
          <input className={inputCls} value={rMax} onChange={(e) => setRMax(e.target.value)} />
        </div>
      )}

      {(kind === 'composition_pie' || kind === 'composition_donut') && (
        <label className="flex items-center gap-2 text-caption text-ink">
          <input type="checkbox" checked={totalAreaEncoding}
            onChange={(e) => setTotalAreaEncoding(e.target.checked)} />
          {t('diagramSymbol.totalAreaEncoding')}
        </label>
      )}

      <div>
        <label className={labelCls}>{t('diagramSymbol.missingPolicy')}</label>
        <select className={inputCls} value={missingPolicy}
          onChange={(e) => setMissingPolicy(e.target.value as 'omit' | 'block')}>
          <option value="omit">{t('diagramSymbol.missing_omit')}</option>
          <option value="block">{t('diagramSymbol.missing_block')}</option>
        </select>
      </div>

      <div className="flex gap-2">
        <button className={btnCls} disabled={busy || !buildBlock()} onClick={() => void runPlan()}>
          {t('diagramSymbol.plan')}
        </button>
        <button className={btnCls} disabled={busy || !plan || plan.status !== 'plan'}
          onClick={() => void runCommit()}>
          {hasExisting ? t('diagramSymbol.update') : t('diagramSymbol.apply')}
        </button>
      </div>

      {planError && (
        <div className="rounded-md border border-status-danger bg-surface p-2 text-caption text-status-danger">
          {planError.code}{planError.field ? ` @${planError.field}` : ''}: {planError.message}
          {planError.hint && <div className="mt-1 text-ink-muted">{planError.hint}</div>}
        </div>
      )}

      {plan?.status === 'plan' && plan.plan?.layout && (
        <div className="rounded-md border border-edge-subtle bg-surface p-2 text-caption">
          <div className="mb-1 font-medium text-ink">{t('diagramSymbol.evidence')}</div>
          {evidence && (
            <div className="grid grid-cols-2 gap-x-3 text-ink-muted">
              <span>{t('diagramSymbol.placed')}: {evidence.placed ?? '–'}</span>
              <span>{t('diagramSymbol.omittedCollision')}: {evidence.omitted_collision ?? '–'}</span>
              <span>{t('diagramSymbol.omittedBudget')}: {evidence.omitted_budget ?? '–'}</span>
              <span>{t('diagramSymbol.blocked')}: {evidence.blocked ?? '–'}</span>
              <span>{t('diagramSymbol.leaderLines')}: {evidence.leader_lines ?? '–'}</span>
              <span>{t('diagramSymbol.missing')}: {evidence.missing_total ?? '–'}</span>
            </div>
          )}
          {legend?.categories && (
            <div className="mt-2">
              <div className="mb-1 text-ink">{t('diagramSymbol.legendPreview')}</div>
              <ul className="space-y-0.5 text-ink-muted">
                {legend.categories.map((c) => <li key={c.category}>{c.category}</li>)}
              </ul>
            </div>
          )}
          {legend?.notes_zh && legend.notes_zh.length > 0 && (
            <ul className="mt-2 list-disc pl-4 text-ink-muted">
              {legend.notes_zh.map((n, i) => <li key={i}>{n}</li>)}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
