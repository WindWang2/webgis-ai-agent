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

/**
 * 契约 instant → 数据表示空间的比较值（语义镜像后端 normalize_boundary_value；
 * 值域不同（两侧指纹算法不同，无交叉比较 —— P2 说明）。
 * 日历字段先转到契约时区再提取（P0：帧桶在契约时区划分，取 UTC 字段会错期）。
 */
export function normalizeBoundaryValue(
  instant: string,
  representation: string,
  granularity: string,
  tzName = 'UTC',
): number | string {
  const dt = parseIso(instant);
  switch (representation) {
    case 'year_int':
      return calendarField(dt, tzName, 'year');
    case 'epoch_s':
      return Math.floor(dt.getTime() / 1000);
    case 'epoch_ms':
      return dt.getTime();
    case 'date_string':
      if (granularity === 'year') return pad(calendarField(dt, tzName, 'year'), 4);
      if (granularity === 'month' || granularity === 'quarter') {
        return `${pad(calendarField(dt, tzName, 'year'), 4)}-${pad(calendarField(dt, tzName, 'month'), 2)}`;
      }
      return `${pad(calendarField(dt, tzName, 'year'), 4)}-${pad(calendarField(dt, tzName, 'month'), 2)}-${pad(
        calendarField(dt, tzName, 'day'), 2,
      )}`;
    default:
      return instant;
  }
}

/** Date 在 IANA 时区的日历字段（year/month/day；tz 缺省 UTC）。 */
function calendarField(dt: Date, tzName: string, field: 'year' | 'month' | 'day'): number {
  let y = dt.getUTCFullYear();
  let m = dt.getUTCMonth() + 1;
  let d = dt.getUTCDate();
  if (tzName && tzName !== 'UTC') {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: tzName, year: 'numeric', month: '2-digit', day: '2-digit',
    }).formatToParts(dt);
    const get = (t: string): number => Number(parts.find((pp) => pp.type === t)?.value);
    y = get('year'); m = get('month'); d = get('day');
  }
  return field === 'year' ? y : field === 'month' ? m : d;
}

function pad(n: number, width: number): string {
  return String(n).padStart(width, '0');
}

/** 帧窗口 → MapLibre filter 表达式（与后端 compile_maplibre_filter 同值）。 */
export function temporalFilterExpression(
  config: MapSpecTemporalConfig,
  frame: TemporalFrame,
): unknown[] {
  const tz = config.timezone || 'UTC';
  const lo = normalizeBoundaryValue(frame.start, config.time_representation, config.granularity, tz);
  const hi = normalizeBoundaryValue(frame.end, config.time_representation, config.granularity, tz);
  return ['all', ['>=', ['get', config.time_field], lo], ['<', ['get', config.time_field], hi]];
}

/** 帧窗口 → where AST（aggregate / 表格分页服务端下推；词表 gte/lt/and）。 */
export function temporalWhereAst(
  config: MapSpecTemporalConfig,
  frame: TemporalFrame,
): WhereAst {
  const tz = config.timezone || 'UTC';
  const lo = normalizeBoundaryValue(frame.start, config.time_representation, config.granularity, tz);
  const hi = normalizeBoundaryValue(frame.end, config.time_representation, config.granularity, tz);
  return {
    op: 'and',
    terms: [
      { op: 'gte', field: config.time_field, value: lo },
      { op: 'lt', field: config.time_field, value: hi },
    ],
  };
}

/** 同窗指纹（前端一致性锚点；与后端 sha256 指纹算法不同、无交叉比较）。 */
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
  // 绑定键族：layer_id ∪ source_ref ∪ ref 别名尾（ref:districts →
  // districts —— agent 以数据名挂层时的常规命名；命中任一即注入）。
  const boundIds = new Set<string>();
  for (const b of config.layers || []) {
    if (b.layer_id) boundIds.add(b.layer_id);
    if (b.source_ref) {
      boundIds.add(b.source_ref);
      const tail = b.source_ref.replace(/^ref:/, '');
      if (tail) boundIds.add(tail);
    }
  }
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
