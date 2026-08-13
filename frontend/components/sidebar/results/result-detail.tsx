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
 */
import { useCallback, useMemo } from 'react';
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

const STATUS_LABEL: Record<string, string> = {
  completed: '已完成',
  failed: '失败',
  partial: '部分完成',
  warning: '完成（含告警）',
  running: '运行中',
  unknown: '未知',
};

const WARNING_VARIANT: Record<string, 'info' | 'warning' | 'error'> = {
  info: 'info',
  warning: 'warning',
  error: 'error',
};

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
      <div className="flex items-center justify-between">
        <h4 className="text-[11px] font-semibold uppercase tracking-wide text-[var(--theme-text-muted)]">{title}</h4>
        {action}
      </div>
      {children}
    </section>
  );
}

function MetricItem({ metric }: { metric: ResultMetric }) {
  return (
    <div
      className={clsx(
        'flex flex-col rounded-md border border-[var(--theme-border-subtle)] bg-[var(--theme-bg-subtle)] px-2 py-1.5',
        metric.emphasis === 'primary' && 'border-[var(--agent-accent,#16a34a)]/40',
      )}
    >
      <span className="text-[10px] text-[var(--theme-text-muted)]">{metric.label}</span>
      <span className={clsx('font-mono text-[var(--theme-text-primary)]', metric.emphasis === 'primary' ? 'text-[15px]' : 'text-[13px]')}>
        {metric.value}
        {metric.unit ? <span className="ml-0.5 text-[10px] text-[var(--theme-text-muted)]">{metric.unit}</span> : null}
      </span>
    </div>
  );
}

function WarningItem({ warning }: { warning: ResultWarning }) {
  return <InlineNotice variant={WARNING_VARIANT[warning.level] ?? 'info'}>{warning.message}</InlineNotice>;
}

export function ResultDetail({ result, sessionId, ownerToken, onBack, onSend }: ResultDetailProps) {
  // Lazy metadata-first enrichment (no full GeoJSON). No-op when already enriched.
  useResultDescriptor(result, sessionId, ownerToken);

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
      switch (action.kind) {
        case 'show_on_map':
        case 'hide':
          if (ref) updateLayer(ref, { visible: !hasVisibleLayer });
          break;
        case 'zoom':
          if (ref) focusLayer(ref);
          break;
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
    [ref, hasVisibleLayer, updateLayer, focusLayer, setActiveLeftTab, onSend, result.toolLabel],
  );

  const crsLabel = output?.crs ?? '未知';
  const featureCountLabel =
    output?.featureCount !== undefined ? output.featureCount.toLocaleString() : '未报告';
  const geomLabel = output?.geometryTypes?.length ? output.geometryTypes.join('、') : '未报告';
  const bboxLabel = output?.bbox ? output.bbox.map((n) => n.toFixed(3)).join(', ') : '未报告';

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto px-3 py-2.5 text-[13px]">
      {/* Header */}
      <div className="flex items-center gap-1.5">
        <IconButton label="返回结果列表" icon={ArrowLeft} onClick={onBack} />
        <div className="flex min-w-0 flex-1 flex-col">
          <span className="truncate font-medium text-[var(--theme-text-primary)]">{result.toolLabel}</span>
          <span className="truncate text-[11px] text-[var(--theme-text-muted)]">
            {familyLabel(result.family)} · {formatTime(result.capturedAt)}
          </span>
        </div>
        <StatusBadge status={result.status} label={STATUS_LABEL[result.status] ?? result.status} />
        <IconButton label="从列表移除" icon={Trash2} variant="ghost" onClick={() => { removeResult(result.id); onBack(); }} />
      </div>

      {/* Summary */}
      {result.summary ? (
        <p className="rounded-md bg-[var(--theme-bg-subtle)] px-2.5 py-2 text-[12.5px] leading-relaxed text-[var(--theme-text-secondary)]">
          {result.summary}
        </p>
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
          <div className="grid grid-cols-2 gap-1.5">
            {result.metrics.map((m, i) => (
              <MetricItem key={`${m.label}-${i}`} metric={m} />
            ))}
          </div>
        </Section>
      ) : null}

      {/* Inputs */}
      <Section title="输入">
        {result.inputs.length > 0 ? (
          <ul className="flex flex-col gap-1">
            {result.inputs.map((inp, i) => (
              <li key={i} className="flex items-baseline justify-between gap-2 text-[12.5px]">
                <span className="text-[var(--theme-text-secondary)]">{inp.label}</span>
                {inp.ref ? (
                  <span className="truncate font-mono text-[10.5px] text-[var(--theme-text-muted)]" title={inp.ref}>{inp.ref}</span>
                ) : (
                  <span className="text-[10.5px] italic text-[var(--theme-text-muted)]">推断</span>
                )}
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-[12px] italic text-[var(--theme-text-muted)]">未捕获输入参数（仅展示操作与输出）。</p>
        )}
      </Section>

      {/* Parameters */}
      {result.parameters.length > 0 ? (
        <Section title="参数">
          <dl className="grid grid-cols-[auto_1fr] gap-x-2.5 gap-y-1 text-[12.5px]">
            {result.parameters.map((p, i) => (
              <div key={i} className="contents">
                <dt className="text-[var(--theme-text-muted)]">{p.label}</dt>
                <dd className="truncate font-mono text-[var(--theme-text-primary)]" title={String(p.value)}>{String(p.value)}</dd>
              </div>
            ))}
          </dl>
        </Section>
      ) : null}

      {/* Output + map linkage */}
      <Section
        title="输出与地图"
        action={
          hasBoundLayer ? (
            <span className="inline-flex items-center gap-1 text-[10.5px] text-[var(--theme-text-muted)]">
              <span
                aria-hidden
                className={clsx('h-1.5 w-1.5 rounded-full', hasVisibleLayer ? 'bg-emerald-500' : 'bg-slate-400')}
              />
              {hasVisibleLayer ? '地图中可见' : '已隐藏'}
            </span>
          ) : null
        }
      >
        <div className="flex flex-col gap-1.5 rounded-md border border-[var(--theme-border-subtle)] bg-[var(--theme-bg-subtle)] px-2.5 py-2 text-[12.5px]">
          <Row label="类型" value={outputKindLabel(output?.kind)} />
          <Row label="要素数" value={featureCountLabel} />
          <Row label="几何类型" value={geomLabel} />
          <Row label="CRS" value={crsLabel} muted={crsLabel === '未知'} />
          <Row label="范围 (W,S,E,N)" value={bboxLabel} mono />
          {output?.estimatedBytes ? <Row label="估算大小" value={formatBytes(output.estimatedBytes)} /> : null}
          {ref ? <Row label="引用" value={ref} mono title={ref} /> : null}
          {output?.note ? <Row label="备注" value={output.note} /> : null}
        </div>

        {/* Map actions */}
        <div className="flex flex-wrap gap-1.5">
          {actions
            .filter((a) => ['show_on_map', 'hide', 'zoom'].includes(a.kind))
            .map((a) => (
              <ActionButton key={a.kind} action={a} onAction={handleAction} />
            ))}
        </div>
      </Section>

      {/* Suggested next actions (analytical intents) */}
      {actions.some((a) => !['show_on_map', 'hide', 'zoom'].includes(a.kind)) ? (
        <Section title="后续操作">
          <div className="flex flex-wrap gap-1.5">
            {actions
              .filter((a) => !['show_on_map', 'hide', 'zoom'].includes(a.kind))
              .map((a) => (
                <ActionButton key={a.kind} action={a} onAction={handleAction} />
              ))}
          </div>
        </Section>
      ) : null}

      {/* Legend (compact; the live legend renders on the map) */}
      {result.legendSpec ? (
        <Section title="图例">
          <span className="text-[12.5px] text-[var(--theme-text-secondary)]">
            {legendSummary(result.legendSpec)}（完整图例见地图）
          </span>
        </Section>
      ) : null}

      {/* Provenance */}
      {result.provenance.length > 0 ? (
        <Section title="数据 lineage">
          <ol className="flex flex-col gap-1 text-[12px] text-[var(--theme-text-secondary)]">
            {result.provenance.map((p, i) => (
              <li key={i} className="flex items-baseline gap-1.5">
                <span className="text-[var(--theme-text-muted)]">{provenanceLabel(p.kind)}</span>
                <span className="truncate text-[var(--theme-text-primary)]">{p.label}</span>
              </li>
            ))}
          </ol>
        </Section>
      ) : null}

      {/* Raw — progressive disclosure */}
      <details className="group rounded-md border border-[var(--theme-border-subtle)] text-[12px]">
        <summary className="cursor-pointer select-none px-2.5 py-1.5 text-[var(--theme-text-muted)] hover:bg-[var(--theme-bg-hover)]">
          原始结果（高级）
        </summary>
        <pre className="max-h-64 overflow-auto px-2.5 py-2 font-mono text-[10.5px] leading-relaxed text-[var(--theme-text-secondary)]">
          {truncateJson(result.raw)}
        </pre>
      </details>
    </div>
  );
}

function Row({ label, value, mono, muted, title }: { label: string; value: string; mono?: boolean; muted?: boolean; title?: string }) {
  return (
    <div className="flex items-baseline justify-between gap-2">
      <span className="text-[var(--theme-text-muted)]">{label}</span>
      <span
        className={clsx('truncate text-right', mono && 'font-mono text-[11.5px]', muted && 'italic text-[var(--theme-text-muted)]')}
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
      className="inline-flex items-center gap-1 rounded-md border border-[var(--theme-border)] bg-[var(--theme-bg)] px-2 py-1 text-[12px] text-[var(--theme-text-secondary)] transition-colors hover:bg-[var(--theme-bg-hover)] disabled:cursor-not-allowed disabled:opacity-40"
    >
      {icon ? <icon.type size={13} aria-hidden /> : null}
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
