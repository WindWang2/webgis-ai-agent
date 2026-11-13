'use client';

/**
 * G33 专题表达工作台（对齐 G20 raster-authoring-panel 模式）。
 *
 * 面向**已有**矢量专题层：字段分布 → 分级方法/人工断点/类表/发散/比例符号 →
 * 预览（零 mutation）→ 单笔提交（与 Pi 同一领域服务、同一 CAS/undo 链）。
 *
 * 纪律：业务校验全部在服务端（本面板只做输入便利与错误定位展示）；
 * 切层/迟到响应由 epoch 守卫丢弃；预览态与已提交态视觉区分；关闭面板
 * 不留下半提交状态（未应用草稿随组件卸载丢弃，原图不变）。
 *
 * 结构纪律：所有 hooks 声明在任何早退 return 之前（React hooks 顺序）。
 */
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { useT } from '@/lib/i18n/useT';
import { useToastStore } from '@/components/ui/toast';
import { getMapSpecSessionCursor, subscribeMapSpecLive } from '@/lib/mapspec/session-cursor';
import {
  asThematicError, commitThematicPlan, inspectThematicLayer, previewThematicPlan,
  type ThematicApiContext,
} from '@/lib/thematic-authoring/api';
import {
  GRADUATED_METHODS, MODES, PALETTES, assignClass,
  type ClassRow, type InspectReceipt, type PlanBody, type PreviewReceipt,
  type ThematicErrorPayload, type ThematicMode,
} from '@/lib/thematic-authoring/contracts';

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

/** 断点草稿行（文本输入允许暂态非法值；提交前由服务端裁决并定位）。 */
interface BreakDraft {
  text: string;
  error?: string;
}

export function ThematicAuthoringPanel({ layerId }: Props) {
  const t = useT('hud');
  const toast = useToastStore((s) => s.addToast);
  const liveRevision = useSyncExternalStore(
    subscribeMapSpecLive, () => getMapSpecSessionCursor().revision);

  const epochRef = useRef(0);
  const [inspect, setInspect] = useState<InspectReceipt | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);

  // ── 作者草稿（面板本地；关闭即丢弃，不改原图） ──
  const [mode, setMode] = useState<ThematicMode>('graduated_auto');
  const [field, setField] = useState<string>('');
  const [method, setMethod] = useState<string>('quantiles');
  const [k, setK] = useState<number>(5);
  const [palette, setPalette] = useState<string>('YlOrRd');
  const [invert, setInvert] = useState(false);
  const [breaksDraft, setBreaksDraft] = useState<BreakDraft[]>([]);
  const [labelsDraft, setLabelsDraft] = useState<string[]>([]);
  const [colorsDraft, setColorsDraft] = useState<string[]>([]);
  const [center, setCenter] = useState<string>('0');
  const [unit, setUnit] = useState<string>('');
  const [nodataColor, setNodataColor] = useState<string>('#cccccc');
  const [nodataLabel, setNodataLabel] = useState<string>('No data');
  const [categories, setCategories] = useState<Array<{ key: string; color: string; label: string }>>([]);
  const [categoryOther, setCategoryOther] = useState<{ color: string; label: string } | null>(null);
  const [sizeEnabled, setSizeEnabled] = useState(false);
  const [sizeChannel, setSizeChannel] = useState<'radius' | 'width'>('radius');
  const [sizeScaling, setSizeScaling] = useState<'linear' | 'area_sqrt'>('linear');
  const [sizeMin, setSizeMin] = useState<string>('4');
  const [sizeMax, setSizeMax] = useState<string>('12');
  const [sizeDomainMin, setSizeDomainMin] = useState<string>('0');
  const [sizeDomainMax, setSizeDomainMax] = useState<string>('100');

  const [preview, setPreview] = useState<PreviewReceipt | null>(null);
  const [previewError, setPreviewError] = useState<ThematicErrorPayload | null>(null);
  const [busy, setBusy] = useState(false);

  // ── 载入 inspect（epoch 守卫：切层迟到响应丢弃——验收 18） ──
  const reload = useCallback(async () => {
    const cursor = getMapSpecSessionCursor();
    if (!cursor.sessionId) return;
    const epoch = ++epochRef.current;
    setLoading(true);
    try {
      const ctx: ThematicApiContext = { sessionId: cursor.sessionId, ownerToken: cursor.ownerToken };
      const receipt = await inspectThematicLayer(ctx, layerId);
      if (epoch !== epochRef.current) return; // 迟到响应：目标层已切换
      setInspect(receipt);
      setLoadError(null);
      // 草稿从当前提交表达初始化
      const spec = receipt.current.legend_spec;
      const f = spec?.field ?? '';
      setField(f);
      setUnit(typeof spec?.unit === 'string' ? spec.unit : '');
      if (spec?.type === 'categorical') {
        setMode('categorical');
        setCategories((spec.categories ?? []).map((c) => ({
          key: c.key, color: c.color, label: c.label ?? c.key,
        })));
      } else if (spec?.type === 'divergent') {
        setMode('divergent');
        setCenter(String(spec.center ?? 0));
      } else if (spec?.type === 'continuous') {
        setMode('continuous');
      } else {
        setMode('graduated_auto');
        setBreaksDraft((spec?.breaks ?? []).map((b) => ({ text: String(b) })));
      }
      if (spec?.palette && (PALETTES as readonly string[]).includes(spec.palette)) {
        setPalette(spec.palette);
      }
      setNodataColor(spec?.nodata?.color ?? '#cccccc');
      setNodataLabel(spec?.nodata?.label ?? 'No data');
      const num = receipt.distribution?.numeric ?? {};
      if (typeof num.min === 'number' && typeof num.max === 'number') {
        setSizeDomainMin(String(num.min));
        setSizeDomainMax(String(num.max));
      }
    } catch (err) {
      if (epoch !== epochRef.current) return;
      const payload = asThematicError(err);
      setLoadError(payload.message);
    } finally {
      if (epoch === epochRef.current) setLoading(false);
    }
  }, [layerId]);

  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => () => { epochRef.current += 1; }, []); // 卸载丢弃在途响应

  // 权威 spec 推进（外部提交/undo）→ 重新对账
  useEffect(() => {
    if (!loading) void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [liveRevision]);

  // ── 计划组装（来源：面板草稿；校验真相在服务端） ──
  const buildPlan = useCallback((): PlanBody | null => {
    const plan: PlanBody = { mode, field: field || null };
    if (mode === 'graduated_auto') {
      plan.method = method;
      plan.k = k;
      plan.palette = palette;
      plan.invert_palette = invert;
      if (labelsDraft.length) plan.labels = labelsDraft;
    } else if (mode === 'graduated_manual') {
      plan.breaks = breaksDraft.map((b) => Number(b.text));
      plan.palette = palette;
      plan.invert_palette = invert;
      if (labelsDraft.length) plan.labels = labelsDraft;
      if (colorsDraft.length) plan.class_colors = colorsDraft;
    } else if (mode === 'categorical') {
      plan.categories = categories;
      plan.category_other = categoryOther;
    } else if (mode === 'continuous') {
      plan.palette = palette;
      plan.invert_palette = invert;
    } else if (mode === 'divergent') {
      plan.divergent_center = Number(center);
      plan.palette = palette;
      plan.invert_palette = invert;
    }
    if (mode !== 'revert_auto') {
      plan.unit = unit || null;
      plan.nodata = { color: nodataColor, label: nodataLabel || 'No data' };
      if (sizeEnabled) {
        plan.size_channel = {
          channel: sizeChannel,
          field: field || inspect?.current.legend_spec?.field || '',
          min_value: Number(sizeDomainMin),
          max_value: Number(sizeDomainMax),
          min_size: Number(sizeMin),
          max_size: Number(sizeMax),
          scaling: sizeScaling,
        };
      }
      const anchor = inspect?.source;
      if (anchor?.ref_id) {
        plan.source_anchor = { ref_id: anchor.ref_id, content_revision: anchor.content_revision };
      }
    }
    return plan;
  }, [mode, field, method, k, palette, invert, breaksDraft, labelsDraft, colorsDraft,
      categories, categoryOther, center, unit, nodataColor, nodataLabel, sizeEnabled,
      sizeChannel, sizeScaling, sizeMin, sizeMax, sizeDomainMin, sizeDomainMax, inspect]);

  const ctx = useMemo((): ThematicApiContext | null => {
    const cursor = getMapSpecSessionCursor();
    if (!cursor.sessionId) return null;
    return { sessionId: cursor.sessionId, ownerToken: cursor.ownerToken };
  }, [liveRevision]);

  const runPreview = useCallback(async () => {
    if (!ctx) return;
    const plan = buildPlan();
    if (!plan) return;
    const epoch = epochRef.current;
    setBusy(true);
    setPreviewError(null);
    try {
      const r = await previewThematicPlan(ctx, layerId, plan);
      if (epoch !== epochRef.current) return;
      setPreview(r);
      // 回填真实类表（服务端裁决后的标签/颜色草稿基线）
      const table = r.class_table ?? [];
      if (r.mode === 'graduated_manual' || r.mode === 'graduated_auto') {
        setLabelsDraft(table.map((c) => c.label ?? ''));
        setColorsDraft(table.map((c) => c.color ?? '#cccccc'));
      }
    } catch (err) {
      if (epoch !== epochRef.current) return;
      setPreview(null);
      setPreviewError(asThematicError(err));
    } finally {
      if (epoch === epochRef.current) setBusy(false);
    }
  }, [ctx, buildPlan, layerId]);

  const runCommit = useCallback(async () => {
    if (!ctx) return;
    const plan = buildPlan();
    if (!plan) return;
    const cursor = getMapSpecSessionCursor();
    const epoch = epochRef.current;
    setBusy(true);
    try {
      await commitThematicPlan(
        ctx, layerId, plan, cursor.revision,
        `g33-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
      if (epoch !== epochRef.current) return;
      toast(t('thematicAuthoring.applied'), 'success');
      setPreview(null);
      setPreviewError(null);
      await reload();
    } catch (err) {
      if (epoch !== epochRef.current) return;
      const payload = asThematicError(err);
      const where = payload.index !== undefined
        ? ` @${payload.field ?? ''}[${payload.index}]` : payload.field ? ` @${payload.field}` : '';
      toast(`${payload.code}${where}: ${payload.message}`, 'error');
    } finally {
      if (epoch === epochRef.current) setBusy(false);
    }
  }, [ctx, buildPlan, layerId, reload, toast, t]);

  const cancelDraft = useCallback(() => {
    setPreview(null);
    setPreviewError(null);
    void reload(); // 草稿丢弃，回到已提交表达
  }, [reload]);

  // ── 派生量（非 hooks 常量；在早退之前，供 localCounts 使用） ──
  const dist = inspect?.distribution;
  const hist = dist?.numeric?.histogram;
  const numeric = dist?.numeric ?? {};
  const breaks = breaksDraft.map((b) => Number(b.text)).filter((n) => Number.isFinite(n));
  const isGraduated = mode === 'graduated_auto' || mode === 'graduated_manual';

  // 本地即时类计数（TS 孪生求值器；权威计数来自服务端预览回执）
  const localCounts = useMemo(() => {
    if (!isGraduated || breaks.length < 2) return null;
    if (!hist || !hist.counts?.length) return null; // 直方图聚合不可用 → 诚实不显示
    const counts = new Array(breaks.length - 1).fill(0);
    for (let i = 0; i < hist.counts.length; i++) {
      const mid = (hist.bin_edges[i] + hist.bin_edges[i + 1]) / 2;
      counts[assignClass(mid, breaks)] += hist.counts[i];
    }
    return counts;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isGraduated, breaks.join(','), hist]);

  const table: ClassRow[] = preview?.class_table ?? [];

  // ── 早退（所有 hooks 之后） ──
  if (loading && !inspect) {
    return <div className="py-3 text-caption text-ink-muted">{t('thematicAuthoring.loading')}</div>;
  }
  if (loadError) {
    return (
      <div className="py-3 space-y-2">
        <p className="text-caption text-status-error">{loadError}</p>
        <button type="button" className={btnCls} onClick={() => void reload()}>
          {t('thematicAuthoring.retry')}
        </button>
      </div>
    );
  }
  if (!inspect) return null;

  if (inspect.shared_expression_scope) {
    return (
      <div className="py-3 text-caption text-ink-muted">
        {t('thematicAuthoring.sharedScope')}
      </div>
    );
  }

  // ── 渲染 ──
  return (
    <div className="space-y-3 py-2" data-testid="thematic-authoring-panel">
      {/* 字段 + 分布 */}
      <div>
        <span className={labelCls}>{t('thematicAuthoring.field')}</span>
        <select
          className={inputCls}
          value={field}
          onChange={(e) => { setField(e.target.value); setPreview(null); }}
        >
          {(inspect.fields ?? []).map((f) => (
            <option key={f.name} value={f.name}>{f.name}</option>
          ))}
        </select>
      </div>

      <div className="rounded-md border border-edge-subtle p-2 space-y-1">
        <div className="flex justify-between text-caption text-ink-muted">
          <span>
            {t('thematicAuthoring.distribution')}
            {dist?.basis?.mode === 'sampled' && (
              <span className="text-status-warning"> · {t('thematicAuthoring.approx')}</span>
            )}
            {dist?.basis?.mode === 'degraded' && (
              <span className="text-status-error"> · {t('thematicAuthoring.degraded')}</span>
            )}
          </span>
          <span>
            {numeric.valid ?? '–'} / {dist?.basis?.total_features ?? '–'} {t('thematicAuthoring.validCount')}
            {numeric.invalid_total ? ` · ${numeric.invalid_total} ${t('thematicAuthoring.invalidCount')}` : ''}
          </span>
        </div>
        {hist && hist.counts?.length > 0 ? (
          <div className="flex items-end gap-px h-12" aria-label="histogram">
            {hist.counts.map((c, i) => {
              const max = Math.max(...hist.counts);
              const h = max > 0 ? Math.round((c / max) * 100) : 0;
              const mid = (hist.bin_edges[i] + hist.bin_edges[i + 1]) / 2;
              const cls = breaks.length >= 2 ? assignClass(mid, breaks) : 0;
              const color = colorsDraft[cls] ?? preview?.legend_spec?.palette_colors?.[cls] ?? '#7aa6c2';
              return (
                <div key={i} className="flex-1 min-w-[2px] rounded-t-sm"
                  style={{ height: `${Math.max(h, 2)}%`, backgroundColor: color, opacity: 0.85 }} />
              );
            })}
          </div>
        ) : (
          <p className="text-caption text-ink-disabled">{t('thematicAuthoring.noHistogram')}</p>
        )}
        {typeof numeric.min === 'number' && (
          <p className="text-caption text-ink-disabled font-mono">
            min {numeric.min} · max {numeric.max} · 0×{numeric.zero ?? 0}
          </p>
        )}
        {(dist?.categorical?.ambiguous_keys?.length ?? 0) > 0 && (
          <p className="text-caption text-status-warning">
            {t('thematicAuthoring.ambiguousKeys')}: {dist!.categorical!.ambiguous_keys!.join(', ')}
          </p>
        )}
      </div>

      {/* 模式 */}
      <div className="grid grid-cols-2 gap-2">
        <div>
          <span className={labelCls}>{t('thematicAuthoring.mode')}</span>
          <select className={inputCls} value={mode}
            onChange={(e) => { setMode(e.target.value as ThematicMode); setPreview(null); }}>
            {MODES.map((m) => (
              <option key={m.id} value={m.id}>{t(m.labelKey)}</option>
            ))}
          </select>
        </div>
        <div>
          <span className={labelCls}>{t('thematicAuthoring.palette')}</span>
          <select className={inputCls} value={palette}
            onChange={(e) => { setPalette(e.target.value); setPreview(null); }}
            disabled={mode === 'categorical' || mode === 'revert_auto'}>
            {PALETTES.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
        </div>
      </div>
      {mode !== 'categorical' && mode !== 'revert_auto' && (
        <label className="flex items-center gap-2 text-caption text-ink-muted">
          <input type="checkbox" checked={invert}
            onChange={(e) => { setInvert(e.target.checked); setPreview(null); }} />
          {t('thematicAuthoring.invert')}
        </label>
      )}

      {/* 分级参数 */}
      {mode === 'graduated_auto' && (
        <div className="grid grid-cols-2 gap-2">
          <div>
            <span className={labelCls}>{t('thematicAuthoring.method')}</span>
            <select className={inputCls} value={method}
              onChange={(e) => { setMethod(e.target.value); setPreview(null); }}>
              {GRADUATED_METHODS.map((m) => (
                <option key={m.id} value={m.id}>{t(m.labelKey)}</option>
              ))}
            </select>
          </div>
          <div>
            <span className={labelCls}>{t('thematicAuthoring.k')}</span>
            <input type="number" min={2} max={12} className={inputCls} value={k}
              onChange={(e) => { setK(Number(e.target.value)); setPreview(null); }} />
          </div>
        </div>
      )}

      {/* 人工断点：精确键盘输入（不靠滑块精度决定最终数字） */}
      {mode === 'graduated_manual' && (
        <div className="space-y-1.5">
          <span className={labelCls}>{t('thematicAuthoring.breaks')}</span>
          {breaksDraft.map((b, i) => (
            <div key={i} className="flex items-center gap-2">
              <input
                className={inputCls + (b.error ? ' border-status-error' : '')}
                inputMode="decimal"
                value={b.text}
                aria-label={`break-${i}`}
                onChange={(e) => {
                  const next = [...breaksDraft];
                  next[i] = { ...next[i], text: e.target.value, error: undefined };
                  // 即时（轻量）相邻校验：相等/倒序定位到具体断点；最终裁决在服务端
                  const v = Number(e.target.value);
                  if (!Number.isFinite(v)) next[i].error = t('thematicAuthoring.breakNaN');
                  else if (i > 0 && Number.isFinite(Number(next[i - 1].text)) && v <= Number(next[i - 1].text)) {
                    next[i].error = t('thematicAuthoring.breakOrder');
                  }
                  setBreaksDraft(next);
                  setPreview(null);
                }}
              />
              {i > 0 && (
                <button type="button" className="text-ink-muted hover:text-ink text-caption"
                  aria-label={`remove-break-${i}`}
                  onClick={() => {
                    setBreaksDraft(breaksDraft.filter((_, j) => j !== i));
                    setPreview(null);
                  }}>✕</button>
              )}
              {i > 0 && i < breaksDraft.length - 1 && (
                <button type="button" className="text-ink-muted hover:text-ink text-caption"
                  aria-label={`add-break-${i}`}
                  onClick={() => {
                    const lo = Number(breaksDraft[i].text);
                    const hi = Number(breaksDraft[i + 1]?.text ?? lo + 1);
                    const mid = Number(((lo + hi) / 2).toFixed(6));
                    const next = [...breaksDraft];
                    next.splice(i + 1, 0, { text: String(mid) });
                    setBreaksDraft(next);
                    setPreview(null);
                  }}>＋</button>
              )}
            </div>
          ))}
          {breaksDraft.some((b) => b.error) && (
            <p className="text-caption text-status-error">
              {t('thematicAuthoring.breakErrors')}
            </p>
          )}
        </div>
      )}

      {/* 发散中心 */}
      {mode === 'divergent' && (
        <div>
          <span className={labelCls}>{t('thematicAuthoring.center')}</span>
          <input className={inputCls} inputMode="decimal" value={center}
            onChange={(e) => { setCenter(e.target.value); setPreview(null); }} />
        </div>
      )}

      {/* 类别表（含顺序调整 + other 桶 + 计数） */}
      {mode === 'categorical' && (
        <div className="space-y-2">
          <span className={labelCls}>{t('thematicAuthoring.categories')}</span>
          {categories.map((c, i) => {
            const stat = inspect.distribution?.categorical?.top?.find((x) => x.key === c.key);
            return (
              <div key={`${c.key}-${i}`} className="rounded-md border border-edge-subtle p-2 space-y-1.5">
                <div className="flex items-center gap-2">
                  <input type="color" value={c.color} aria-label={`cat-color-${i}`}
                    onChange={(e) => {
                      const next = [...categories];
                      next[i] = { ...c, color: e.target.value };
                      setCategories(next); setPreview(null);
                    }} />
                  <span className="font-mono text-caption text-ink flex-1 truncate">{c.key}</span>
                  {stat && <span className="text-caption text-ink-disabled">{stat.count}</span>}
                  <button type="button" aria-label={`cat-up-${i}`} disabled={i === 0}
                    className="text-ink-muted hover:text-ink disabled:opacity-30"
                    onClick={() => {
                      const next = [...categories];
                      [next[i - 1], next[i]] = [next[i], next[i - 1]];
                      setCategories(next); setPreview(null);
                    }}>↑</button>
                  <button type="button" aria-label={`cat-down-${i}`} disabled={i === categories.length - 1}
                    className="text-ink-muted hover:text-ink disabled:opacity-30"
                    onClick={() => {
                      const next = [...categories];
                      [next[i + 1], next[i]] = [next[i], next[i + 1]];
                      setCategories(next); setPreview(null);
                    }}>↓</button>
                  <button type="button" aria-label={`cat-remove-${i}`}
                    className="text-ink-muted hover:text-ink"
                    onClick={() => {
                      setCategories(categories.filter((_, j) => j !== i)); setPreview(null);
                    }}>✕</button>
                </div>
                <input className={inputCls} value={c.label} aria-label={`cat-label-${i}`}
                  placeholder={c.key}
                  onChange={(e) => {
                    const next = [...categories];
                    next[i] = { ...c, label: e.target.value };
                    setCategories(next); setPreview(null);
                  }} />
              </div>
            );
          })}
          <div className="flex items-center gap-2">
            <button type="button" className={btnCls}
              onClick={() => {
                setCategories([...categories, { key: '', color: '#888888', label: '' }]);
                setPreview(null);
              }}>{t('thematicAuthoring.addCategory')}</button>
            <label className="flex items-center gap-1.5 text-caption text-ink-muted">
              <input type="checkbox" checked={categoryOther !== null}
                onChange={(e) => {
                  setCategoryOther(
                    e.target.checked
                      ? { color: '#cccccc', label: t('thematicAuthoring.otherDefault') }
                      : null);
                  setPreview(null);
                }} />
              {t('thematicAuthoring.otherBucket')}
            </label>
          </div>
          {categoryOther && (
            <div className="flex items-center gap-2">
              <input type="color" value={categoryOther.color}
                onChange={(e) => { setCategoryOther({ ...categoryOther, color: e.target.value }); setPreview(null); }} />
              <input className={inputCls} value={categoryOther.label}
                onChange={(e) => { setCategoryOther({ ...categoryOther, label: e.target.value }); setPreview(null); }} />
            </div>
          )}
          {(inspect.distribution?.categorical?.other_count ?? 0) > 0 && !categoryOther && (
            <p className="text-caption text-status-warning">
              {inspect.distribution!.categorical!.other_count} {t('thematicAuthoring.unlistedWarn')}
            </p>
          )}
        </div>
      )}

      {/* 大小/线宽映射 */}
      {mode !== 'revert_auto' && (
        <div className="rounded-md border border-edge-subtle p-2 space-y-2">
          <label className="flex items-center gap-2 text-caption text-ink-muted">
            <input type="checkbox" checked={sizeEnabled}
              onChange={(e) => { setSizeEnabled(e.target.checked); setPreview(null); }} />
            {t('thematicAuthoring.sizeChannel')}
          </label>
          {sizeEnabled && (
            <div className="grid grid-cols-2 gap-2">
              <div>
                <span className={labelCls}>{t('thematicAuthoring.channel')}</span>
                <select className={inputCls} value={sizeChannel}
                  onChange={(e) => {
                    const v = e.target.value as 'radius' | 'width';
                    setSizeChannel(v);
                    if (v === 'width') setSizeScaling('linear');
                    setPreview(null);
                  }}>
                  <option value="radius">{t('thematicAuthoring.radius')}</option>
                  <option value="width">{t('thematicAuthoring.width')}</option>
                </select>
              </div>
              <div>
                <span className={labelCls}>{t('thematicAuthoring.scaling')}</span>
                <select className={inputCls} value={sizeScaling} disabled={sizeChannel === 'width'}
                  onChange={(e) => { setSizeScaling(e.target.value as 'linear' | 'area_sqrt'); setPreview(null); }}>
                  <option value="linear">{t('thematicAuthoring.linear')}</option>
                  <option value="area_sqrt">{t('thematicAuthoring.areaSqrt')}</option>
                </select>
              </div>
              {(['sizeDomainMin', 'sizeDomainMax', 'sizeMin', 'sizeMax'] as const).map((key) => (
                <div key={key}>
                  <span className={labelCls}>{t(`thematicAuthoring.${key}`)}</span>
                  <input className={inputCls} inputMode="decimal"
                    value={{ sizeDomainMin, sizeDomainMax, sizeMin, sizeMax }[key]}
                    onChange={(e) => {
                      const v = e.target.value;
                      if (key === 'sizeDomainMin') setSizeDomainMin(v);
                      if (key === 'sizeDomainMax') setSizeDomainMax(v);
                      if (key === 'sizeMin') setSizeMin(v);
                      if (key === 'sizeMax') setSizeMax(v);
                      setPreview(null);
                    }} />
                </div>
              ))}
              {sizeScaling === 'area_sqrt' && (
                <p className="col-span-2 text-caption text-ink-disabled">
                  {t('thematicAuthoring.areaNote')}
                </p>
              )}
            </div>
          )}
        </div>
      )}

      {/* 单位 / nodata */}
      {mode !== 'revert_auto' && (
        <div className="grid grid-cols-2 gap-2">
          <div>
            <span className={labelCls}>{t('thematicAuthoring.unit')}</span>
            <input className={inputCls} value={unit} maxLength={24}
              onChange={(e) => { setUnit(e.target.value); setPreview(null); }} />
          </div>
          <div>
            <span className={labelCls}>{t('thematicAuthoring.nodata')}</span>
            <div className="flex gap-1.5">
              <input type="color" value={nodataColor}
                onChange={(e) => { setNodataColor(e.target.value); setPreview(null); }} />
              <input className={inputCls} value={nodataLabel} maxLength={32}
                onChange={(e) => { setNodataLabel(e.target.value); setPreview(null); }} />
            </div>
          </div>
        </div>
      )}

      {/* 预览回执：类表 + 披露（与已提交态视觉区分） */}
      {previewError && (
        <div className="rounded-md border border-status-error bg-status-error/5 p-2 text-caption text-status-error space-y-1">
          <p className="font-mono">{previewError.code}{previewError.index !== undefined ? ` [${previewError.index}]` : ''}</p>
          <p>{previewError.message}</p>
          {previewError.correction_hint && <p className="text-ink-muted">{previewError.correction_hint}</p>}
        </div>
      )}
      {preview && (
        <div className="rounded-md border border-status-accent/60 bg-status-accent/5 p-2 space-y-1.5" data-testid="thematic-preview">
          <div className="flex justify-between text-caption">
            <span className="text-status-accent">{t('thematicAuthoring.previewBadge')}</span>
            {preview.requested_k !== null && preview.actual_k !== null
              && preview.requested_k !== preview.actual_k && (
              <span className="text-status-warning">
                {t('thematicAuthoring.kDegraded', {
                  requested: preview.requested_k ?? 0,
                  actual: preview.actual_k ?? 0,
                })}
              </span>
            )}
          </div>
          {table.map((c) => (
            <div key={c.index} className="flex items-center gap-2 text-caption">
              <span className="inline-block w-3.5 h-3.5 rounded-sm border border-edge-subtle"
                style={{ backgroundColor: c.color ?? '#ccc' }} />
              <span className="flex-1 truncate">{c.label ?? c.key}</span>
              <span className="text-ink-disabled">{c.count ?? '–'}</span>
            </div>
          ))}
          {preview.size_samples?.map((s) => (
            <div key={s.anchor} className="flex items-center gap-2 text-caption">
              <span className="inline-block rounded-full border border-edge-subtle bg-surface"
                style={{ width: s.size * 2, height: s.size * 2 }} />
              <span className="font-mono text-ink-disabled">{s.anchor} {s.value} → {s.size.toFixed(1)}px</span>
            </div>
          ))}
          {preview.disclosures?.map((d, i) => (
            <p key={i} className="text-caption text-ink-muted">· {d}</p>
          ))}
        </div>
      )}

      {/* 动作 */}
      <div className="flex gap-2">
        <button type="button" className={btnCls} disabled={busy} onClick={() => void runPreview()}>
          {t('thematicAuthoring.previewBtn')}
        </button>
        <button type="button" className={btnCls + ' border-status-accent text-status-accent'}
          disabled={busy || !preview} onClick={() => void runCommit()}>
          {t('thematicAuthoring.applyBtn')}
        </button>
        <button type="button" className={btnCls} disabled={busy} onClick={cancelDraft}>
          {t('thematicAuthoring.cancelBtn')}
        </button>
        {mode !== 'revert_auto' && (
          <button type="button" className={btnCls} disabled={busy}
            onClick={() => {
              setMode('revert_auto');
              setPreview(null);
            }}>
            {t('thematicAuthoring.revertBtn')}
          </button>
        )}
      </div>
      {mode === 'revert_auto' && (
        <p className="text-caption text-ink-muted">{t('thematicAuthoring.revertNote')}</p>
      )}
      {localCounts && !preview && isGraduated && (
        <p className="text-caption text-ink-disabled">
          {t('thematicAuthoring.localCounts')}: {localCounts.join(' / ')}
        </p>
      )}
    </div>
  );
}
