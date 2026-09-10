/**
 * V6（ADR-0120 W5）：legend_spec → 图例条目模型的**唯一推导源**。
 *
 * 此前条目推导散落四处且语义漂移（R1-M7 六点 diff 表见
 * .agent-work/cartography-v6/05-legend-convergence-diff-table.md）：
 * - live legends.tsx legendEntries（graduated honors labels；categorical
 *   丢弃无色条目；continuous 无分支）
 * - vector-svg-export legendItemsOf（cap + labels；#888 兜底）
 * - export-chrome drawChromeLegend（无 cap、忽略 labels、'未知字段' 标题）
 * - render-scene oracle legendEntryCount（数量口径）
 *
 * 收敛后各消费端只做**呈现**（画布/卡片/DOM），不再各自推导条目。
 * 采纳口径：graduated cap+labels（live/vector 语义）、categorical 无色
 * 以 #888 兜底（不静默丢数据）、范围标签 formatLegendValue（live 语义，
 * zh-CN 感知）、标题 title || 字段: || 图例。
 *
 * 纯函数、无副作用；后端镜像 = app/lib/cartography/render_scene.py
 * derive_legend_items，跨语言 parity 由共享 golden fixtures 锁定。
 */
import { formatLegendValue } from '@/components/map/legends/legend-card';
import type { LegendSpec } from '@/lib/map-kit/types';

export interface LegendModelEntry {
  label: string;
  color: string;
  /** class = 数据类目；nodata = 无数据规则条目（恒在末尾）。 */
  kind: 'class' | 'nodata';
}

export type LegendModelKind = 'categorical' | 'graduated' | 'continuous' | 'bivariate';

export interface LegendModel {
  kind: LegendModelKind;
  title: string;
  /** nodata 条目已追加在末尾（与 live legendEntries 同序）。 */
  entries: LegendModelEntry[];
  hasNodata: boolean;
}

const FALLBACK_COLOR = '#888';

/** 标题兜底单源（模型 null 时 oracle/快照仍需标题语义）。 */
export function deriveLegendTitle(spec: { title?: string; field?: string } | null | undefined): string {
  if (!spec) return '图例';
  return spec.title || (spec.field ? `字段: ${spec.field}` : '图例');
}

function titleOf(spec: { title?: string; field?: string }): string {
  return deriveLegendTitle(spec);
}

function pickContinuousColor(colors: string[], t: number): string {
  return (
    colors[Math.min(colors.length - 1, Math.round(t * (colors.length - 1)))] || FALLBACK_COLOR
  );
}

/**
 * legend_spec → 图例模型。返回 null 表示"无可呈现条目"（消费端不画空卡）。
 * bivariate 有专用渲染器（色阵），模型只给条目（含 nodata 语义计数）。
 */
export function deriveLegendModel(spec: LegendSpec | undefined | null): LegendModel | null {
  if (!spec) return null;
  const s = spec as LegendSpec & {
    nodata?: { color?: string; label?: string };
    entries?: unknown;
    labels?: unknown[];
    title?: string;
    field?: string;
    unit?: string;
  };

  const entries: LegendModelEntry[] = [];
  const nodata = s.nodata?.color
    ? { label: s.nodata.label || '无数据', color: s.nodata.color }
    : null;

  // 遗留通道：部分工具直接写 entries[]（live legendEntries 既有透传语义）。
  const legacy = s.entries;
  if (Array.isArray(legacy)) {
    for (const e of legacy as Array<{ color?: string; label?: string }>) {
      if (e && typeof e.color === 'string' && e.color) {
        entries.push({
          color: e.color,
          label: e.label != null && String(e.label).trim() !== '' ? String(e.label) : '',
          kind: 'class',
        });
      }
    }
    if (nodata) entries.push({ ...nodata, kind: 'nodata' });
    if (entries.length === 0) return null;
    const type = typeof s.type === 'string' ? s.type : 'categorical';
    return {
      kind: (type as LegendModelKind) ?? 'categorical',
      title: titleOf(s),
      entries,
      hasNodata: !!nodata,
    };
  }

  if (spec.type === 'bivariate') {
    // 专用色阵渲染器消费；通用条目仅承载 nodata 语义（oracle 计数同源）。
    if (nodata) entries.push({ ...nodata, kind: 'nodata' });
    return { kind: 'bivariate', title: titleOf(s), entries, hasNodata: !!nodata };
  }

  if (spec.type === 'categorical') {
    for (const c of (spec.categories ?? []) as Array<{ color?: string; label?: string; key?: string }>) {
      entries.push({
        label:
          c?.label != null && String(c.label).trim() !== '' ? String(c.label) : String(c?.key ?? ''),
        color: c?.color || FALLBACK_COLOR,
        kind: 'class',
      });
    }
  } else if (spec.type === 'graduated') {
    const breaks = spec.breaks ?? [];
    const colors = spec.palette_colors ?? [];
    const n = Math.min(Math.max(breaks.length - 1, 0), colors.length);
    for (let i = 0; i < n; i++) {
      const override = s.labels?.[i];
      entries.push({
        label:
          override != null && String(override).trim() !== ''
            ? String(override)
            : `${formatLegendValue(breaks[i])} – ${formatLegendValue(breaks[i + 1])}`,
        color: colors[i] || FALLBACK_COLOR,
        kind: 'class',
      });
    }
  } else {
    // continuous / divergent：min/mid/max 三读数（drawChromeColorbar 同口径）。
    const min = (spec as { min?: unknown }).min;
    const max = (spec as { max?: unknown }).max;
    if (typeof min === 'number' && typeof max === 'number') {
      const colors = (spec as { palette_colors?: string[] }).palette_colors ?? [];
      const mid = (min + max) / 2;
      entries.push({ label: formatLegendValue(min), color: pickContinuousColor(colors, 0), kind: 'class' });
      entries.push({ label: formatLegendValue(mid), color: pickContinuousColor(colors, 0.5), kind: 'class' });
      entries.push({ label: formatLegendValue(max), color: pickContinuousColor(colors, 1), kind: 'class' });
    }
    // min/max 缺失 → 无类目条目（nodata 仍在场时保留披露）。
  }

  if (nodata) entries.push({ ...nodata, kind: 'nodata' });
  if (entries.length === 0) return null;
  return {
    kind: spec.type === 'graduated' ? 'graduated' : spec.type === 'categorical' ? 'categorical' : 'continuous',
    title: titleOf(s),
    entries,
    hasNodata: !!nodata,
  };
}
