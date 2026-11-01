/**
 * temporal-map 过滤编译器（GOAL 03 M4）。
 *
 * 帧窗口 [start,end) → MapLibre filter / where AST / fingerprint —— 与后端
 * app/services/gis_harness/temporal_products/time_filter.py 同一语义镜像
 * （表示归一：字典序安全仅在探测保证的同格式/同偏移下成立，契约已固化
 * time_representation）。地图（layer.filter）、图表（aggregate where）、
 * 表格（分页 where）共用本编译器产物 → 同窗结构性保证（Oracle 4）。
 */
import type { MapSpec, MapSpecTemporalConfig, TemporalFrame } from '@/lib/mapspec-compiler/types';

export type WhereAst = Record<string, unknown>;

function parseIso(instant: string): Date {
  const dt = new Date(instant);
  if (Number.isNaN(dt.getTime())) throw new Error(`invalid temporal instant: ${instant}`);
  return dt;
}

/** 契约 instant → 数据表示空间的比较值（镜像 normalize_boundary_value）。 */
export function normalizeBoundaryValue(
  instant: string,
  representation: string,
  granularity: string,
): number | string {
  const dt = parseIso(instant);
  switch (representation) {
    case 'year_int':
      return dt.getUTCFullYear();
    case 'epoch_s':
      return Math.floor(dt.getTime() / 1000);
    case 'epoch_ms':
      return dt.getTime();
    case 'date_string':
      if (granularity === 'year') return String(dt.getUTCFullYear());
      if (granularity === 'month' || granularity === 'quarter') {
        return `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, '0')}`;
      }
      return `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, '0')}-${String(
        dt.getUTCDate(),
      ).padStart(2, '0')}`;
    default:
      return instant;
  }
}

/** 帧窗口 → MapLibre filter 表达式（与后端 compile_maplibre_filter 同值）。 */
export function temporalFilterExpression(
  config: MapSpecTemporalConfig,
  frame: TemporalFrame,
): unknown[] {
  const lo = normalizeBoundaryValue(frame.start, config.time_representation, config.granularity);
  const hi = normalizeBoundaryValue(frame.end, config.time_representation, config.granularity);
  return ['all', ['>=', ['get', config.time_field], lo], ['<', ['get', config.time_field], hi]];
}

/** 帧窗口 → where AST（aggregate / 表格分页服务端下推；词表 gte/lt/and）。 */
export function temporalWhereAst(
  config: MapSpecTemporalConfig,
  frame: TemporalFrame,
): WhereAst {
  const lo = normalizeBoundaryValue(frame.start, config.time_representation, config.granularity);
  const hi = normalizeBoundaryValue(frame.end, config.time_representation, config.granularity);
  return {
    op: 'and',
    args: [
      { op: 'gte', field: config.time_field, value: lo },
      { op: 'lt', field: config.time_field, value: hi },
    ],
  };
}

/** 同窗指纹（地图/图表/表格/导出一致性锚点；镜像后端 time_filter_fingerprint）。 */
export function temporalFingerprint(
  config: MapSpecTemporalConfig,
  frameId: string,
): string {
  const payload = [
    config.source_ref,
    config.content_revision ?? '',
    config.time_field,
    frameId,
    config.time_representation,
  ].join('|');
  let h1 = 0x811c9dc5;
  let h2 = 0x01000193;
  for (let i = 0; i < payload.length; i += 1) {
    const c = payload.charCodeAt(i);
    h1 = (h1 ^ c) * 16777619 >>> 0;
    h2 = (h2 + c * 31) >>> 0;
  }
  return `${h1.toString(16)}${h2.toString(16)}`.slice(0, 16);
}

function andFilters(a: unknown[] | undefined, b: unknown[]): unknown[] {
  if (!a || a.length === 0) return b;
  return ['all', a, b];
}

/**
 * 把当前帧时间过滤注入 composed spec 的绑定层 layer.filter。
 *
 * 纯函数（新对象；不改输入）—— map-panel reconcile effect 消费；runtime
 * V4 filter 快路径（setFilter-only）使帧切换零结构 churn、零数据重拉。
 * frame 覆写参数 = 播放/拖动中的 live 帧（缺省用 spec cursor）。
 * 绑定层不存在于 spec.layers → 原样返回（诚实：绑定错层不伪造过滤）。
 */
export function applyTemporalToSpec(
  spec: MapSpec,
  config: MapSpecTemporalConfig,
  frame?: TemporalFrame | null,
): MapSpec {
  const boundIds = new Set((config.layers || []).map((b) => b.layer_id));
  if (boundIds.size === 0) return spec;
  const frames = config.frames || [];
  const effective = frame
    ?? frames.find((f) => f.id === config.cursor_frame)
    ?? null;
  if (!effective) return spec;
  const expr = temporalFilterExpression(config, effective);
  let touched = false;
  const layers = (spec.layers || []).map((layer) => {
    if (!layer.id || !boundIds.has(layer.id)) return layer;
    touched = true;
    return {
      ...layer,
      filter: andFilters(layer.filter as unknown[] | undefined, expr),
    };
  });
  if (!touched) return spec;
  return { ...spec, layers };
}
