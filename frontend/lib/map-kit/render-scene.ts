/**
 * V5（ADR-0118 W10）：canonical render scene 语义投影。
 *
 * 把一张（live 合成后的或 committed 的）MapSpec 投影为**语义快照**——
 * 图层可见性、chrome 组件（经 resolveMapComponents 单点解析）、图例
 * 家族（条目数/nodata）、label 通道、降级码清单。纯函数、确定性排序，
 * 作为 live ↔ export 语义 parity 与 golden corpus 的共同 oracle：
 * 断言语义（什么在场、什么可见、披露什么）而非像素。
 */
import { resolveMapComponents } from '@/lib/map-components/resolve-components';
import type { MapSpec } from '@/lib/mapspec-compiler/types';
import type { ExportDegradation } from './export-chrome';

export interface RenderSceneLayer {
  id: string;
  visible: boolean;
  /** legend_spec 在场 = 专题层有图例通道。 */
  hasLegendSpec: boolean;
  /** label/text 通道（spec 层 label 或 layout.labelField）。 */
  hasLabel: boolean;
}

export interface RenderSceneComponent {
  id: string;
  type: string;
  position: string;
  variant: string;
  enabled: boolean;
  collapsed: boolean;
}

export interface RenderSceneLegend {
  layerId: string;
  title: string;
  entryCount: number;
  hasNodata: boolean;
}

export interface RenderSceneSnapshot {
  layers: RenderSceneLayer[];
  components: RenderSceneComponent[];
  legends: RenderSceneLegend[];
  degradationCodes: string[];
}

/** LegendSpec 条目数口径（与 legends.tsx legendEntries / vector-svg legendItemsOf 同语义）。 */
function legendEntryCount(legendSpec: unknown): number {
  const ls = legendSpec as {
    type?: string;
    categories?: unknown[];
    breaks?: number[];
    palette_colors?: string[];
    min?: number;
    max?: number;
    nodata?: { color?: string };
  } | null;
  if (!ls || typeof ls !== 'object') return 0;
  let count = 0;
  if (ls.type === 'categorical') count = (ls.categories ?? []).length;
  else if (ls.type === 'graduated') {
    count = Math.min(Math.max((ls.breaks?.length ?? 0) - 1, 0), (ls.palette_colors ?? []).length);
  } else if (typeof ls.min === 'number' && typeof ls.max === 'number') count = 3;
  if (ls.nodata?.color) count += 1;
  return count;
}

function legendTitle(legendSpec: unknown): string {
  const ls = (legendSpec ?? {}) as { title?: string; field?: string };
  return ls.title || (ls.field ? `字段: ${ls.field}` : '图例');
}

/**
 * MapSpec → 语义快照。组件经 resolveMapComponents（与 live/export 共享的
 * 唯一解析器）；图层与图例按 spec 字面投影；排序确定性（id 字典序），
 * 降级码去重排序。
 */
export function describeRenderScene(
  spec: MapSpec | null | undefined,
  opts: { degradations?: ExportDegradation[] } = {},
): RenderSceneSnapshot {
  const layers: RenderSceneLayer[] = Array.isArray(spec?.layers)
    ? spec.layers
        .filter((l): l is NonNullable<typeof l> => !!l && typeof l === 'object' && typeof l.id === 'string')
        .map((l) => {
          const layout = (l.layout ?? {}) as { visibility?: string; labelField?: string };
          const label = (l as { label?: unknown }).label;
          const legendSpec = (l as { legend_spec?: unknown }).legend_spec;
          return {
            id: l.id,
            visible: layout.visibility !== 'none' && l.visible !== false,
            hasLegendSpec: !!legendSpec && typeof legendSpec === 'object',
            hasLabel: !!label || !!layout.labelField,
          };
        })
        .sort((a, b) => (a.id < b.id ? -1 : a.id > b.id ? 1 : 0))
    : [];

  const components: RenderSceneComponent[] = resolveMapComponents(spec)
    .map((c) => ({
      id: c.id ?? '',
      type: c.type,
      position: c.anchor,
      variant: c.variant,
      enabled: c.enabled,
      collapsed: c.collapsed,
    }))
    .sort((a, b) => a.id.localeCompare(b.id) || a.type.localeCompare(b.type));

  const legends: RenderSceneLegend[] = Array.isArray(spec?.layers)
    ? spec.layers
        .filter(
          (l): l is NonNullable<typeof l> & { legend_spec: unknown } =>
            !!l && typeof l === 'object' &&
            typeof (l as { id?: unknown }).id === 'string' &&
            !!(l as { legend_spec?: unknown }).legend_spec,
        )
        .map((l) => ({
          layerId: l.id,
          title: legendTitle(l.legend_spec),
          entryCount: legendEntryCount(l.legend_spec),
          hasNodata: !!(l.legend_spec as { nodata?: { color?: string } }).nodata?.color,
        }))
        .sort((a, b) => a.layerId.localeCompare(b.layerId))
    : [];

  const degradationCodes = Array.from(
    new Set((opts.degradations ?? []).map((d) => d.code)),
  ).sort();

  return { layers, components, legends, degradationCodes };
}

/** 规范化序列化（golden corpus 落盘口径：稳定键序 + 2 空格缩进）。 */
export function serializeRenderScene(snapshot: RenderSceneSnapshot): string {
  return JSON.stringify(snapshot, null, 2);
}
