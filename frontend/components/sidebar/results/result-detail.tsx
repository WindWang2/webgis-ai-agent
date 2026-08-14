'use client';

/**
 * ResultDetail — the canonical GIS analysis result inspector (spec §7).
 *
 * Composed entirely of reusable semantic sections + shared primitives (no
 * per-family components). Map actions go through the store (`updateLayer`,
 * `focusLayer`) — never `setViewport`, never direct MapLibre. Output metadata is
 * enriched lazily by `useResultDescriptor` (metadata-first, no full GeoJSON).
 *
 * Truthfulness: CRS renders "未知" when unknown; feature count renders "未报告"
 * when not backed by evidence; warnings are always visible (never buried in JSON).
 *
 * UI V4 — header is a persistent compact bar outside the scroll area (context
 * survives long scrolls); 输出与地图 leads with a layer strip that fuses the
 * live visibility state with the show/hide + zoom controls, so the result→map
 * relationship is one visual unit; analytical suggestions render as secondary
 * text intents, never mixed with map controls. A failed result drops the
 * output/actions sections — its content is the correction hint.
 */
import { useCallback, useEffect, useMemo, useRef } from 'react';
import {
  ArrowLeft,
  Eye,
  EyeOff,
  MapPinned,
  Download,
  Layers as LayersIcon,
  Sliders,
  Crosshair,
  Trash2,
} from 'lucide-react';
import clsx from 'clsx';
import { useHudStore } from '@/lib/store/useHudStore';
import { useResultDescriptor } from '@/lib/hooks/use-result-descriptor';
import { deriveSuggestedActions } from '@/lib/results/suggested-actions';
import { familyLabel } from '@/lib/results/families';
import type { AnalysisResult, ResultMetric, ResultWarning, SuggestedAction } from '@/lib/results/types';
import { StatusBadge } from '@/components/shared/status-badge';
import { InlineNotice } from '@/components/shared/inline-notice';
import { IconButton } from '@/components/shared/icon-button';

interface ResultDetailProps {
  result: AnalysisResult;
  sessionId?: string | null;
  ownerToken?: string | null;
  onBack: () => void;
  onSend: (text: string) => void;
}

const WARNING_VARIANT: Record<string, 'info' | 'warning' | 'error'> = {
  info: 'info',
  warning: 'warning',
  error: 'error',
};

const MAP_ACTION_KINDS: readonly SuggestedAction['kind'][] = ['show_on_map', 'hide', 'zoom'];

function formatTime(ms?: number): string {
  if (!ms) return '';
  try {
    return new Date(ms).toLocaleString(undefined, {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return '';
  }
}

function formatBytes(bytes?: number): string {
  if (bytes === undefined || bytes === null) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function Section({ title, children, action }: { title: string; children: React.ReactNode; action?: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-1.5" aria-label={title}>
      <div className="flex items-center justify-between gap-2">
        <h4 className="eyebrow">{title}</h4>
        {action}
      </div>
      {children}
    </section>
  );
}

function MetricItem({ metric }: { metric: ResultMetric }) {
  const primary = metric.emphasis === 'primary';
  if (primary) {
    return (
      <div className="col-span-2 flex items-baseline justify-between gap-2 rounded-sm border border-status-accent-border px-2 py-1.5">
        <span className="text-caption text-ink-muted">{metric.label}</span>
        <span className="min-w-0 truncate font-mono text-heading text-ink">
          {metric.value}
          {metric.unit ? <span className="ml-1 text-micro font-normal text-ink-muted">{metric.unit}</span> : null}
        </span>
      </div>
    );
  }
  return (
    <div className="flex items-baseline justify-between gap-2 px-0.5">
      <span className="min-w-0 truncate text-caption text-ink-muted">{metric.label}</span>
      <span className="min-w-0 truncate font-mono text-body text-ink">
        {metric.value}
        {metric.unit ? <span className="ml-1 text-micro text-ink-muted">{metric.unit}</span> : null}
      </span>
    </div>
  );
}

function WarningItem({ warning }: { warning: ResultWarning }) {
  return <InlineNotice variant={WARNING_VARIANT[warning.level] ?? 'info'}>{warning.message}</InlineNotice>;
}

export function ResultDetail({ result, sessionId, ownerToken, onBack, onSend }: ResultDetailProps) {
  // A failed result has no inspectable output — its content is the correction
  // hint. Skip the descriptor enrichment for it (nothing to enrich) and drop
  // the output/actions sections below instead of rendering rows of 未报告.
  const failed = result.status === 'failed';

  // Lazy metadata-first enrichment (no full GeoJSON). No-op when already enriched.
  useResultDescriptor(failed ? null : result, sessionId, ownerToken);

  const layers = useHudStore((s) => s.layers);
  const updateLayer = useHudStore((s) => s.updateLayer);
  const focusLayer = useHudStore((s) => s.focusLayer);
  const setActiveLeftTab = useHudStore((s) => s.setActiveLeftTab);
  const removeResult = useHudStore((s) => s.removeResult);

  const output = result.outputs[0];
  const ref = output?.ref;

  // Live layer binding (store truth, not the normalizer's static hint).
  const boundLayer = useMemo(
    () => (ref ? layers.find((l) => l.id === ref || l._refId === ref) ?? null : null),
    [layers, ref],
  );
  const hasBoundLayer = !!boundLayer;
  const hasVisibleLayer = !!boundLayer?.visible;

  const actions = useMemo(
    () => deriveSuggestedActions(result.family, result.outputs, hasVisibleLayer, hasBoundLayer),
    [result.family, result.outputs, hasVisibleLayer, hasBoundLayer],
  );

  const handleAction = useCallback(
    (action: SuggestedAction) => {
      if (!action.available) return;
      // Resolve through the bound layer's real id (not the raw ref) so the
      // `_refId` binding the UI honors is the same one the writes target.
      const layerId = boundLayer?.id ?? ref;
      switch (action.kind) {
        case 'show_on_map':
        case 'hide': {
          if (!layerId) return;
          // Absolute value from the action kind: two rapid clicks before a
          // re-render must not compute the same target twice.
          updateLayer(layerId, { visible: action.kind === 'show_on_map' });
          break;
        }
        case 'zoom': {
          if (!layerId) return;
          focusLayer(layerId);
          break;
        }
        case 'style':
          setActiveLeftTab('layers');
          break;
        case 'buffer':
          onSend(`对刚生成的「${result.toolLabel}」结果做缓冲区分析`);
          break;
        case 'overlay':
          onSend(`将「${result.toolLabel}」结果与其他图层进行叠加分析`);
          break;
        case 'classify':
          onSend(`对「${result.toolLabel}」栅格结果进行分类`);
          break;
        case 'inspect':
          onSend(`检查「${result.toolLabel}」结果中的显著要素`);
          break;
        case 'export':
          onSend(`导出「${result.toolLabel}」结果`);
          break;
      }
    },
    [ref, boundLayer, updateLayer, focusLayer, setActiveLeftTab, onSend, result.toolLabel],
  );

  const crsLabel = output?.crs ?? '未知';
  const featureCountLabel =
    output?.featureCount !== undefined ? output.featureCount.toLocaleString() : '未报告';
  const geomLabel = output?.geometryTypes?.length ? output.geometryTypes.join('、') : '未报告';
  const bboxLabel = output?.bbox ? output.bbox.map((n) => n.toFixed(3)).join(', ') : '未报告';

  const showOutputSection = !failed;
  // Partition once: map controls render with the layer strip they act on;
  // analytical intents are a separate, secondary group.
  const mapActions = actions.filter((a) => MAP_ACTION_KINDS.includes(a.kind));
  const toggleAction = mapActions.find((a) => a.kind === 'show_on_map' || a.kind === 'hide');
  const zoomAction = mapActions.find((a) => a.kind === 'zoom');
  const analyticalActions = actions.filter((a) => !MAP_ACTION_KINDS.includes(a.kind));

  // Focus contract (drill-in): landing in the detail puts keyboard users on
  // the first control — Back. The ring only paints for keyboard-origin focus
  // (`:focus-visible`), so mouse users see no change.
  const backRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    backRef.current?.focus();
  }, []);

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      {/* Header — persistent compact bar (outside the scroll area, so the
          result identity + status survive any scroll depth). */}
      <header className="flex shrink-0 items-center gap-1.5 border-b border-edge-subtle px-panel py-1">
        <IconButton ref={backRef} label="返回结果列表" icon={ArrowLeft} size="sm" onClick={onBack} />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate text-title font-medium leading-tight text-ink">{result.toolLabel}</span>
          <span className="truncate text-caption leading-tight text-ink-muted">
            {[familyLabel(result.family), formatTime(result.capturedAt)].filter(Boolean).join(' · ')}
          </span>
        </div>
        <StatusBadge status={result.status} />
        <IconButton
          label="从列表移除"
          icon={Trash2}
          size="sm"
          variant="ghost"
          onClick={() => {
            removeResult(result.id);
            onBack();
          }}
        />
      </header>

      <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-panel py-2 text-body">
        {/* Summary */}
        {result.summary ? (
          <p className="leading-normal text-ink-secondary">{result.summary}</p>
        ) : null}

        {/* Warnings — always visible, never buried */}
        {result.warnings.length > 0 ? (
          <Section title="告警 / 提示">
            <div className="flex flex-col gap-1.5">
              {result.warnings.map((w) => (
                <WarningItem key={w.code} warning={w} />
              ))}
            </div>
          </Section>
        ) : null}

        {/* Key metrics */}
        {result.metrics.length > 0 ? (
          <Section title="关键指标">
            <div className="grid grid-cols-2 gap-x-2 gap-y-1">
              {result.metrics.map((m, i) => (
                <MetricItem key={`${m.label}-${i}`} metric={m} />
              ))}
            </div>
          </Section>
        ) : null}

        {/* Inputs */}
        <Section title="输入">
          {result.inputs.length > 0 ? (
            <ul className="flex flex-col gap-0.5">
              {result.inputs.map((inp, i) => (
                <li key={i} className="flex items-baseline justify-between gap-2 text-meta">
                  <span className="shrink-0 text-ink-secondary">{inp.label}</span>
                  {inp.ref ? (
                    <span className="min-w-0 truncate font-mono text-caption text-ink-muted" title={inp.ref}>
                      {inp.ref}
                    </span>
                  ) : (
                    <span className="text-caption italic text-ink-muted">推断</span>
                  )}
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-meta italic text-ink-muted">未捕获输入参数（仅展示操作与输出）。</p>
          )}
        </Section>

        {/* Parameters */}
        {result.parameters.length > 0 ? (
          <Section title="参数">
            <dl className="grid grid-cols-[auto_1fr] gap-x-2.5 gap-y-0.5 text-meta">
              {result.parameters.map((p, i) => (
                <div key={i} className="contents">
                  <dt className="text-ink-muted">{p.label}</dt>
                  <dd className="min-w-0 truncate font-mono text-ink" title={String(p.value)}>
                    {String(p.value)}
                  </dd>
                </div>
              ))}
            </dl>
          </Section>
        ) : null}

        {/* Output + map linkage — the layer strip fuses live visibility with
            the map controls, then the metadata sheet follows. */}
        {showOutputSection ? (
          <Section title="输出与地图">
            {hasBoundLayer ? (
              <div className="flex items-center gap-1 rounded-md border border-edge-subtle bg-surface-raised px-1 py-0.5">
                {toggleAction ? (
                  <IconButton
                    label={toggleAction.label}
                    icon={hasVisibleLayer ? EyeOff : Eye}
                    size="sm"
                    disabled={!toggleAction.available}
                    onClick={() => handleAction(toggleAction)}
                  />
                ) : null}
                <span className="min-w-0 flex-1 truncate px-0.5 font-mono text-caption text-ink" title={boundLayer?.name}>
                  {boundLayer?.name ?? ref}
                </span>
                <span className="inline-flex shrink-0 items-center gap-1 text-caption text-ink-muted">
                  <span
                    aria-hidden
                    className={clsx(
                      'h-1 w-1 rounded-pill',
                      hasVisibleLayer ? 'bg-status-accent-vivid' : 'bg-ink-disabled',
                    )}
                  />
                  {hasVisibleLayer ? '地图中可见' : '已隐藏'}
                </span>
                {zoomAction ? (
                  <IconButton
                    label={zoomAction.label}
                    icon={Crosshair}
                    size="sm"
                    disabled={!zoomAction.available}
                    onClick={() => handleAction(zoomAction)}
                  />
                ) : null}
              </div>
            ) : (
              <p className="text-meta text-ink-muted">
                {ref ? '引用层未绑定到当前地图会话。' : '该结果未挂载为地图图层。'}
              </p>
            )}

            <div className="flex flex-col gap-0.5 rounded-md bg-surface-sunken px-2.5 py-1.5 text-meta">
              <Row label="类型" value={outputKindLabel(output?.kind)} />
              <Row label="要素数" value={featureCountLabel} />
              <Row label="几何类型" value={geomLabel} />
              <Row label="CRS" value={crsLabel} muted={crsLabel === '未知'} />
              {/* bbox/ref are data, not prose — wrap instead of truncating so a
                  coordinate is never silently cut mid-number. */}
              <Row label="范围 (W,S,E,N)" value={bboxLabel} mono wrap />
              {output?.estimatedBytes ? <Row label="估算大小" value={formatBytes(output.estimatedBytes)} /> : null}
              {ref ? <Row label="引用" value={ref} mono wrap title={ref} /> : null}
              {output?.note ? <Row label="备注" value={output.note} /> : null}
            </div>
          </Section>
        ) : null}

        {/* Suggested next actions — analytical intents, secondary to map controls */}
        {!failed && analyticalActions.length > 0 ? (
          <Section title="后续操作">
            <div className="flex flex-wrap gap-1">
              {analyticalActions.map((a) => (
                <ActionButton key={a.kind} action={a} onAction={handleAction} />
              ))}
            </div>
          </Section>
        ) : null}

        {/* Legend (compact; the live legend renders on the map) */}
        {result.legendSpec ? (
          <Section title="图例">
            <span className="text-meta text-ink-secondary">
              {legendSummary(result.legendSpec)}（完整图例见地图）
            </span>
          </Section>
        ) : null}

        {/* Provenance */}
        {result.provenance.length > 0 ? (
          <Section title="数据溯源">
            <ol className="flex flex-col gap-0.5 text-meta text-ink-secondary">
              {result.provenance.map((p, i) => (
                <li key={i} className="flex items-baseline gap-1.5">
                  <span className="shrink-0 text-ink-muted">{provenanceLabel(p.kind)}</span>
                  <span className="min-w-0 truncate text-ink">{p.label}</span>
                </li>
              ))}
            </ol>
          </Section>
        ) : null}

        {/* Raw — progressive disclosure */}
        <details className="rounded-sm border border-edge-subtle text-meta">
          <summary className="cursor-pointer select-none px-2 py-1 text-ink-muted transition-colors hover:bg-surface-hover">
            原始结果（高级）
          </summary>
          <pre className="max-h-64 overflow-auto px-2 py-1.5 font-mono text-caption leading-relaxed text-ink-secondary">
            {truncateJson(result.raw)}
          </pre>
        </details>
      </div>
    </div>
  );
}

function Row({
  label,
  value,
  mono,
  muted,
  wrap,
  title,
}: {
  label: string;
  value: string;
  mono?: boolean;
  muted?: boolean;
  wrap?: boolean;
  title?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="shrink-0 text-ink-muted">{label}</span>
      <span
        className={clsx(
          'min-w-0 text-right text-ink',
          mono && 'font-mono text-caption',
          wrap ? 'break-words' : 'truncate',
          muted && 'italic text-ink-muted',
        )}
        title={title ?? value}
      >
        {value}
      </span>
    </div>
  );
}

function ActionButton({ action, onAction }: { action: SuggestedAction; onAction: (a: SuggestedAction) => void }) {
  const icon = actionIcon(action.kind);
  return (
    <button
      type="button"
      disabled={!action.available}
      onClick={() => onAction(action)}
      aria-label={action.label}
      className="inline-flex h-control-sm items-center gap-1 rounded-sm px-1.5 text-meta text-ink-secondary transition-colors hover:bg-surface-hover hover:text-ink disabled:cursor-not-allowed disabled:opacity-40"
    >
      {icon ? <icon.type size={12} aria-hidden /> : null}
      {action.label}
    </button>
  );
}

function actionIcon(kind: SuggestedAction['kind']): { type: typeof Eye } | null {
  switch (kind) {
    case 'show_on_map': return { type: Eye };
    case 'hide': return { type: EyeOff };
    case 'zoom': return { type: Crosshair };
    case 'style': return { type: Sliders };
    case 'buffer': return { type: MapPinned };
    case 'overlay': return { type: LayersIcon };
    case 'classify': return { type: LayersIcon };
    case 'inspect': return { type: Crosshair };
    case 'export': return { type: Download };
    default: return null;
  }
}

function outputKindLabel(kind?: string): string {
  const map: Record<string, string> = {
    vector: '矢量', raster: '栅格', statistic: '统计', table: '表格', image: '图像', none: '—',
  };
  return kind ? map[kind] ?? kind : '—';
}

function legendSummary(spec: AnalysisResult['legendSpec']): string {
  if (!spec) return '';
  const typeMap: Record<string, string> = { graduated: '分级', continuous: '连续', categorical: '分类', divergent: '发散' };
  const field = (spec as { field?: string }).field;
  return `${typeMap[spec.type] ?? spec.type}${field ? ` · ${field}` : ''}`;
}

function provenanceLabel(kind: string): string {
  const map: Record<string, string> = { input: '输入', operation: '操作', output: '输出', run: '运行' };
  return map[kind] ?? kind;
}

function truncateJson(raw: unknown): string {
  try {
    const s = JSON.stringify(raw, null, 2);
    return s.length > 4000 ? `${s.slice(0, 4000)}\n…（已截断）` : s;
  } catch {
    return String(raw);
  }
}

export default ResultDetail;
