/**
 * G21 OD flow product —— Studio 面板的类型、API 边界与纯投影。
 *
 * 纪律：
 * - 本文件是唯一网络边界（inspect/preview/query/ref geojson 拉取），
 *   组件测试只 mock 本模块，不模拟核心聚合。
 * - 投影函数全部纯函数：committed MapSpec → 候选源 / 已应用产品 /
 *   预览 MapSpec / 统计视图，便于 TDD。
 * - 预览永不写 MapSpec；apply 走既有 commitMapSpecMutation（CAS）。
 */

import { ApiError, apiFetch } from '@/lib/api/transport';
import type { MapSpec, MapSpecLayerFlow } from '@/lib/mapspec-compiler/types';

/* ── 类型（与后端 FlowProductConfig / 响应契约对齐） ─────────────────────── */

export type FlowDirectionMode = 'directed' | 'undirected-total' | 'net';
export type FlowGeometry = 'straight' | 'curve';
export type FlowWeightTransform = 'linear' | 'sqrt' | 'log1p';
export type FlowMissingReverse = 'unknown' | 'zero';
export type FlowNodeTotalsScope = 'filtered' | 'thresholded';
export type FlowNodeDirection = 'inbound' | 'outbound' | 'both';

export interface FlowProductConfig {
  od_table_ref: string;
  origin_id_field: string;
  origin_lng_field: string;
  origin_lat_field: string;
  destination_id_field: string;
  destination_lng_field: string;
  destination_lat_field: string;
  weight_field: string;
  weight_unit: string;
  value_semantics: 'auto' | 'flow' | 'cost';
  node_table_ref?: string | null;
  node_id_field: string;
  node_lng_field: string;
  node_lat_field: string;
  source_crs: string;
  time_field?: string | null;
  time_start?: string | null;
  time_end?: string | null;
  timezone: string;
  category_field?: string | null;
  category_values?: string[] | null;
  dimension_fields: string[];
  direction_mode: FlowDirectionMode;
  missing_reverse: FlowMissingReverse;
  net_reference: 'node_id_asc';
  geometry: FlowGeometry;
  show_arrows: boolean;
  top_n: number;
  min_weight: number;
  normalize: boolean;
  curvature: number;
  arrow_spacing_px: number;
  arrow_size_px: number;
  width_min_px: number;
  width_max_px: number;
  weight_transform: FlowWeightTransform;
  weight_domain?: [number, number] | null;
  palette?: string[] | null;
  show_nodes: boolean;
  node_totals_scope: FlowNodeTotalsScope;
  product_id?: string | null;
}

export function defaultFlowConfig(overrides: Partial<FlowProductConfig> = {}): FlowProductConfig {
  return {
    od_table_ref: '',
    origin_id_field: 'origin_id',
    origin_lng_field: 'origin_lng',
    origin_lat_field: 'origin_lat',
    destination_id_field: 'destination_id',
    destination_lng_field: 'destination_lng',
    destination_lat_field: 'destination_lat',
    weight_field: 'weight',
    weight_unit: '',
    value_semantics: 'auto',
    node_table_ref: null,
    node_id_field: 'node_id',
    node_lng_field: 'node_lng',
    node_lat_field: 'node_lat',
    source_crs: 'EPSG:4326',
    time_field: null,
    time_start: null,
    time_end: null,
    timezone: 'UTC',
    category_field: null,
    category_values: null,
    dimension_fields: [],
    direction_mode: 'directed',
    missing_reverse: 'unknown',
    net_reference: 'node_id_asc',
    geometry: 'straight',
    show_arrows: true,
    top_n: 500,
    min_weight: 0,
    normalize: true,
    curvature: 0.2,
    arrow_spacing_px: 40,
    arrow_size_px: 10,
    width_min_px: 1,
    width_max_px: 8,
    weight_transform: 'linear',
    weight_domain: null,
    palette: null,
    show_nodes: true,
    node_totals_scope: 'filtered',
    product_id: null,
    ...overrides,
  };
}

export interface FlowInspectResponse {
  success: boolean;
  operation: string;
  source_ref: string;
  source_revision: number;
  source_kind?: string;
  row_count?: number;
  crs?: string | null;
  value_semantics?: string;
  fields: Record<string, string>;
  sample: Record<string, unknown>[];
  sample_truncated?: boolean;
}

export interface FlowStats {
  rows?: { raw?: number; valid?: number; filtered?: number; excluded?: number };
  weights?: { raw?: number; valid?: number; filtered?: number };
  edges?: { aggregated?: number; thresholded?: number; displayed?: number };
  gross?: { population?: number; displayed?: number; retained?: number | null };
  net?: {
    population_magnitude?: number;
    displayed_magnitude?: number;
    magnitude_coverage?: number | null;
  };
  nodes?: { total?: number; displayed?: number };
}

export interface FlowModeAvailabilityEntry {
  available: boolean;
  reason?: string;
  unknown_pairs?: number;
}

export interface FlowMapSpecFragment {
  sources: Record<string, { type: string; url?: string; ref_id?: string; ref?: string }>;
  layers: Array<Record<string, unknown>>;
}

export interface FlowPreviewResponse {
  success: boolean;
  operation: string;
  product_ref: string;
  family_id: string;
  source_ref: string;
  source_revision: number;
  source_fingerprint?: string;
  product_fingerprint?: string;
  cache_hit?: string;
  aggregation_reused?: boolean;
  edge_ref?: string;
  node_ref?: string;
  stats?: FlowStats;
  supported_modes?: FlowDirectionMode[];
  mode_availability?: Partial<Record<FlowDirectionMode, FlowModeAvailabilityEntry>>;
  warnings?: string[];
  degradations?: string[];
  can_apply?: boolean;
  blocking_reasons?: string[];
  mapspec_fragment?: FlowMapSpecFragment;
  mapspec_revision?: number;
}

export interface FlowPage<T = Record<string, unknown>> {
  items: T[];
  offset: number;
  limit: number;
  matched_count: number;
  next_offset: number | null;
}

export interface FlowQueryNodeResponse {
  success: boolean;
  kind: 'node';
  product_ref: string;
  node: Record<string, unknown>;
  direction: FlowNodeDirection;
  totals_scope?: string;
  edges: FlowPage;
}

export interface FlowQueryEdgeResponse {
  success: boolean;
  kind: 'edge' | 'members';
  product_ref: string;
  edge: Record<string, unknown>;
  members: FlowPage;
}

export type FlowQueryResponse = FlowQueryNodeResponse | FlowQueryEdgeResponse;

/* ── API 边界（组件测试 mock 的唯一网络面） ──────────────────────────────── */

interface FlowRequestOptions {
  ownerToken?: string | null;
  signal?: AbortSignal;
}

export async function inspectFlowSource(
  sessionId: string,
  config: FlowProductConfig,
  opts: FlowRequestOptions = {},
): Promise<FlowInspectResponse> {
  return apiFetch<FlowInspectResponse>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/flow-product/inspect`,
    {
      method: 'POST',
      body: { config },
      ownerToken: opts.ownerToken,
      signal: opts.signal,
      timeoutMs: 60_000,
      label: 'OD flow inspect',
    },
  );
}

export async function previewFlowProduct(
  sessionId: string,
  config: FlowProductConfig,
  opts: FlowRequestOptions = {},
): Promise<FlowPreviewResponse> {
  return apiFetch<FlowPreviewResponse>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/flow-product/preview`,
    {
      method: 'POST',
      body: { config },
      ownerToken: opts.ownerToken,
      signal: opts.signal,
      timeoutMs: 120_000,
      label: 'OD flow preview',
    },
  );
}

export async function queryFlowProduct(
  sessionId: string,
  req: {
    product_ref: string;
    kind: 'product' | 'node' | 'edge' | 'members';
    object_id?: string;
    direction?: FlowNodeDirection;
    offset?: number;
    limit?: number;
  },
  opts: FlowRequestOptions = {},
): Promise<FlowQueryResponse> {
  return apiFetch<FlowQueryResponse>(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/flow-product/query`,
    {
      method: 'POST',
      body: {
        product_ref: req.product_ref,
        kind: req.kind,
        object_id: req.object_id ?? '',
        direction: req.direction ?? 'both',
        offset: req.offset ?? 0,
        limit: req.limit ?? 50,
      },
      ownerToken: opts.ownerToken,
      signal: opts.signal,
      timeoutMs: 60_000,
      label: 'OD flow query',
    },
  );
}

/** 按 ref 拉取 geojson（edge/node 产物），供预览 SVG 编译 —— 只读，不写 MapSpec。 */
export async function fetchFlowRefGeoJSON(
  sessionId: string,
  ref: string,
  opts: FlowRequestOptions = {},
): Promise<{ type: string; features: unknown[] }> {
  return apiFetch<{ type: string; features: unknown[] }>(
    `/api/v1/layers/data/${encodeURIComponent(ref)}?session_id=${encodeURIComponent(sessionId)}`,
    { ownerToken: opts.ownerToken, signal: opts.signal, timeoutMs: 60_000, label: 'OD flow ref geojson' },
  );
}

/* ── 纯投影 ─────────────────────────────────────────────────────────────── */

export interface FlowSourceCandidate {
  /** committed MapSpec sources 的键（面板展示名）。 */
  key: string;
  /** 可回取/可 inspect 的 ref（geojson url）。 */
  ref: string;
}

/**
 * 候选 OD 表：committed MapSpec 中 type=geojson 且带 url ref 的源；
 * 已被 flow 产品层消费的源（产品自身的 edge/node 产物）排除 —— 它们是
 * 派生产物，不是候选输入。
 */
export function listFlowSourceCandidates(spec: MapSpec | null | undefined): FlowSourceCandidate[] {
  if (!spec?.sources) return [];
  const consumed = new Set(
    (spec.layers ?? []).filter((l) => l.flow).flatMap((l) => [
      l.source,
      `flow-${l.flow!.family_id}-edges-source`,
      `flow-${l.flow!.family_id}-nodes-source`,
    ]),
  );
  const out: FlowSourceCandidate[] = [];
  for (const [key, src] of Object.entries(spec.sources)) {
    if (!src || src.type !== 'geojson') continue;
    if (consumed.has(key)) continue;
    const candidate = src.url ?? (src as unknown as { ref_id?: unknown; ref?: unknown }).ref_id ?? (src as unknown as { ref?: unknown }).ref;
    const ref = typeof candidate === 'string' && candidate.startsWith('ref:') ? candidate : null;
    if (!ref) continue;
    out.push({ key, ref });
  }
  return out;
}

export interface AppliedFlow {
  layerId: string;
  flow: MapSpecLayerFlow;
}

/** 刷新恢复：committed MapSpec 中第一个带 layer.flow 的层即已应用产品。 */
export function findAppliedFlow(spec: MapSpec | null | undefined): AppliedFlow | null {
  for (const layer of spec?.layers ?? []) {
    if (layer.flow) return { layerId: layer.id, flow: layer.flow };
  }
  return null;
}

/** 从已应用 layer.flow 恢复可编辑配置（显式绑定字段/口径缺失时保留缺省）。 */
export function flowConfigFromApplied(flow: MapSpecLayerFlow): FlowProductConfig {
  const encoding = (flow.encoding ?? {}) as Record<string, unknown>;
  const filters = (flow.filters ?? {}) as Record<string, unknown>;
  const num = (v: unknown): number | undefined =>
    typeof v === 'number' && Number.isFinite(v) ? v : undefined;
  return defaultFlowConfig({
    od_table_ref: flow.source_ref,
    weight_field: flow.source_weight_field || 'weight',
    weight_unit: flow.weight_unit || '',
    direction_mode: flow.direction_mode,
    geometry: flow.geometry,
    show_arrows: flow.show_arrows,
    node_totals_scope: flow.node_totals_scope,
    weight_transform: (encoding['weight_transform'] as FlowWeightTransform) ?? 'linear',
    weight_domain: Array.isArray(encoding['weight_domain'])
      ? [Number(encoding['weight_domain'][0]), Number(encoding['weight_domain'][1])]
      : null,
    width_min_px: num(encoding['width_min_px']) ?? 1,
    width_max_px: num(encoding['width_max_px']) ?? 8,
    arrow_spacing_px: num(encoding['arrow_spacing_px']) ?? 40,
    arrow_size_px: num(encoding['arrow_size_px']) ?? 10,
    top_n: num(filters['top_n']) ?? 500,
    min_weight: num(filters['min_weight']) ?? 0,
    show_nodes: true,
    product_id: null,
  });
}

/**
 * 由 inspect 真实返回的字段表猜测显式绑定（用户确认后才用于 preview）。
 * 精确名（大小写不敏感）优先，其次子串包含；无命中给空串（UI 显示未识别，
 * 不伪造）。
 */
export function guessFieldBindings(
  fields: Record<string, string>,
): Partial<Record<
  | 'origin_id_field' | 'origin_lng_field' | 'origin_lat_field'
  | 'destination_id_field' | 'destination_lng_field' | 'destination_lat_field'
  | 'weight_field',
  string
>> {
  const names = Object.keys(fields);
  const pick = (exact: string[], contains: string[]): string => {
    const lower = new Map(names.map((n) => [n.toLowerCase(), n]));
    for (const candidate of exact) {
      const hit = lower.get(candidate);
      if (hit) return hit;
    }
    const hit = names.find((n) => {
      const ln = n.toLowerCase();
      return contains.some((c) => ln.includes(c));
    });
    return hit ?? '';
  };
  return {
    origin_id_field: pick(['origin_id', 'o_id', 'from_id'], ['origin_id', 'o_id', 'from_id']),
    origin_lng_field: pick(['origin_lng', 'origin_lon', 'o_lng', 'o_lon', 'from_lng', 'from_lon'], ['origin_lng', 'origin_lon', 'from_lng', 'from_lon']),
    origin_lat_field: pick(['origin_lat', 'o_lat', 'from_lat'], ['origin_lat', 'from_lat']),
    destination_id_field: pick(['destination_id', 'd_id', 'to_id'], ['destination_id', 'd_id', 'to_id']),
    destination_lng_field: pick(['destination_lng', 'destination_lon', 'd_lng', 'd_lon', 'to_lng', 'to_lon'], ['destination_lng', 'destination_lon', 'to_lng', 'to_lon']),
    destination_lat_field: pick(['destination_lat', 'd_lat', 'to_lat'], ['destination_lat', 'to_lat']),
    weight_field: pick(['weight', 'flow', 'count', 'volume', 'trips', 'passengers', 'value'], ['weight', 'flow', 'count', 'volume', 'trip', 'passenger']),
  };
}

/**
 * 预览 MapSpec：fragment 的 url 源替换为真实拉回的 inlineData 后交给
 * compileMapSpecToSvg —— 不 monkeypatch 编译器，绝不 commit 到 MapSpec。
 */
export function buildPreviewMapSpec(
  fragment: FlowMapSpecFragment,
  edgeGeoJson: { type: string; features: unknown[] } | null,
  nodeGeoJson: { type: string; features: unknown[] } | null,
): MapSpec {
  const sources: MapSpec['sources'] = {};
  for (const [key, src] of Object.entries(fragment.sources ?? {})) {
    if (!src || src.type !== 'geojson') continue;
    // 后缀判定：family_id 自身含 "nodes"（如 x_nodes）时 edges 源键
    // flow-x_nodes-edges-source 不得被误判为节点源。
    // 后缀判定：family_id 自身含 "nodes"（如 x_nodes）时 edges 源键
    // flow-x_nodes-edges-source 不得被误判为节点源。
    const isNode = key.endsWith('-nodes-source');
    const data = isNode ? nodeGeoJson : edgeGeoJson;
    sources[key] = {
      type: 'geojson',
      ...(data ? { inlineData: data } : {}),
      ...(data ? {} : { url: src.url }),
    };
  }
  return {
    version: 'flow-preview.v1',
    sources,
    layers: fragment.layers as unknown as MapSpec['layers'],
  };
}

export interface FlowStatsView {
  rowsRaw?: number;
  rowsValid?: number;
  rowsFiltered?: number;
  rowsExcluded?: number;
  edgesAggregated?: number;
  edgesThresholded?: number;
  edgesDisplayed?: number;
  grossPopulation?: number;
  grossDisplayed?: number;
  grossRetained?: number | null;
  netPopulationMagnitude?: number;
  netDisplayedMagnitude?: number;
  netMagnitudeCoverage?: number | null;
  nodesTotal?: number;
  nodesDisplayed?: number;
}

/** stats → 展示模型（不同名称保留：gross retained ≠ net magnitude coverage）。 */
export function flowStatsView(stats: FlowStats | null | undefined): FlowStatsView {
  if (!stats) return {};
  return {
    rowsRaw: stats.rows?.raw,
    rowsValid: stats.rows?.valid,
    rowsFiltered: stats.rows?.filtered,
    rowsExcluded: stats.rows?.excluded,
    edgesAggregated: stats.edges?.aggregated,
    edgesThresholded: stats.edges?.thresholded,
    edgesDisplayed: stats.edges?.displayed,
    grossPopulation: stats.gross?.population,
    grossDisplayed: stats.gross?.displayed,
    grossRetained: stats.gross?.retained ?? null,
    netPopulationMagnitude: stats.net?.population_magnitude,
    netDisplayedMagnitude: stats.net?.displayed_magnitude,
    netMagnitudeCoverage: stats.net?.magnitude_coverage ?? null,
    nodesTotal: stats.nodes?.total,
    nodesDisplayed: stats.nodes?.displayed,
  };
}

export interface FlowModeView {
  mode: FlowDirectionMode;
  available: boolean;
  reason?: string;
  unknownPairs?: number;
}

/** mode_availability → 单选组视图：net 不可用时禁用并附后端理由。 */
export function modeAvailabilityView(
  supportedModes: FlowDirectionMode[] | undefined,
  availability: Partial<Record<FlowDirectionMode, FlowModeAvailabilityEntry>> | undefined,
): FlowModeView[] {
  const modes: FlowDirectionMode[] = ['directed', 'undirected-total', 'net'];
  const supported = new Set(supportedModes ?? ['directed', 'undirected-total']);
  return modes.map((mode) => {
    const entry = availability?.[mode];
    const available = entry ? entry.available : supported.has(mode);
    return {
      mode,
      available,
      ...(entry?.reason ? { reason: entry.reason } : {}),
      ...(typeof entry?.unknown_pairs === 'number' ? { unknownPairs: entry.unknown_pairs } : {}),
    };
  });
}

export function committedLayerBbox(
  spec: MapSpec | null | undefined,
  layerId: string | undefined,
): [number, number, number, number] | null {
  const layer = spec?.layers?.find((item) => item.id === layerId);
  if (!layer) return null;
  const source = spec?.sources?.[layer.source] as { profile?: { bbox?: unknown } } | undefined;
  const bbox = source?.profile?.bbox;
  if (!Array.isArray(bbox) || bbox.length !== 4 || !bbox.every((n) => typeof n === 'number' && Number.isFinite(n))) return null;
  return bbox as [number, number, number, number];
}

/**
 * committed MapSpec 中属于 OD flow 产品族的平铺层 id（edges/direction/nodes）。
 *
 * flow 产品层由后端直写 MapSpec（平铺 id，不走 HUD 的 `${id}__${sub}` 展开），
 * 因此 `computeInteractiveIds` 的 `__` 过滤不会覆盖它们 —— 点击查询面必须
 * 显式并入这些 id，节点/边焦点才能真实命中。
 */
export function flowFamilyLayerIds(spec: MapSpec | null | undefined): string[] {
  const layers = spec?.layers;
  if (!Array.isArray(layers)) return [];
  const out: string[] = [];
  for (const layer of layers) {
    const familyId = (layer as { metadata?: { flow_family_id?: unknown } }).metadata?.flow_family_id;
    if (familyId && layer.id) out.push(String(layer.id));
  }
  return out;
}

/** ApiError detail {error, message} → 可展示码+消息；非该形态返回 null。 */
export function flowErrorDetail(err: unknown): { code: string; message: string } | null {
  if (!(err instanceof ApiError)) return null;
  const body = err.body as { detail?: unknown } | null;
  const detail = body?.detail;
  if (detail && typeof detail === 'object') {
    const d = detail as { error?: unknown; message?: unknown };
    if (typeof d.message === 'string' && d.message) {
      return {
        code: typeof d.error === 'string' ? d.error : `http_${err.status}`,
        message: d.message,
      };
    }
  }
  return null;
}
