/**
 * Analysis-family classification + metric extraction rules.
 *
 * This is the data-driven "matrix": each family declares how to read *scalar*
 * result fields for metrics. It reads ONLY scalars — it never touches
 * `result.data.features` (which may be a large array, or already stripped by the
 * server-side slim_event_result). Missing fields are skipped, never fabricated.
 */
import type { OutputKind, ResultFamily, ResultMetric, StepResultEvent } from './types';

/* ─── Tool labels (localized; falls back to humanized name) ─────────────────── */

const TOOL_LABELS: Record<string, string> = {
  buffer_analysis: '缓冲区分析',
  multi_ring_buffer: '多环缓冲区',
  service_area_simple: '服务区域',
  overlay_analysis: '叠加分析',
  spatial_stats: '空间统计',
  moran_i: "Moran's I 空间自相关",
  standard_deviational_ellipse: '标准差椭圆',
  hotspot_analysis: '热点分析',
  spatial_cluster: '空间聚类',
  st_dbscan: '时空聚类 (ST-DBSCAN)',
  kde_surface: '核密度表面',
  kde_contours: '核密度等值线',
  heatmap_data: '热力图',
  idw_interpolation: 'IDW 插值',
  isochrone_network: '等时圈分析',
  nearest_facility: '最近设施',
  raster_reclassify: '栅格重分类',
  raster_calculator: '栅格计算器',
  raster_resample: '栅格重采样',
  zonal_stats: '分区统计',
  h3_binning: 'H3 聚合',
  h3_lisa: 'H3 LISA',
  compute_ndvi: 'NDVI 计算',
  fetch_dem: 'DEM 获取',
  create_thematic_map: '专题制图',
  apply_template: '制图模板',
  display_layer: '图层显示',
};

const FAMILY_LABELS: Record<ResultFamily, string> = {
  buffer: '邻近分析',
  overlay: '叠加分析',
  spatial_stats: '空间统计',
  hotspot: '热点分析',
  cluster: '聚类分析',
  density: '密度分析',
  interpolation: '插值分析',
  network: '网络分析',
  raster: '栅格分析',
  h3: '六边形 (H3) 分析',
  remote_sensing: '遥感分析',
  cartography: '制图',
  generic: '空间分析',
};

const FAMILY_BY_TOOL: Record<string, ResultFamily> = {
  buffer_analysis: 'buffer',
  multi_ring_buffer: 'buffer',
  service_area_simple: 'buffer',
  overlay_analysis: 'overlay',
  spatial_stats: 'spatial_stats',
  moran_i: 'spatial_stats',
  standard_deviational_ellipse: 'spatial_stats',
  hotspot_analysis: 'hotspot',
  spatial_cluster: 'cluster',
  st_dbscan: 'cluster',
  kde_surface: 'density',
  kde_contours: 'density',
  heatmap_data: 'density',
  idw_interpolation: 'interpolation',
  isochrone_network: 'network',
  nearest_facility: 'network',
  raster_reclassify: 'raster',
  raster_calculator: 'raster',
  raster_resample: 'raster',
  // zonal_stats outputs a VECTOR FeatureCollection (polygons enriched with raster
  // stats), so it must NOT be classified as raster (which would force kind=raster).
  h3_binning: 'h3',
  h3_lisa: 'h3',
  compute_ndvi: 'remote_sensing',
  fetch_dem: 'remote_sensing',
  create_thematic_map: 'cartography',
  apply_template: 'cartography',
};

export function classifyFamily(tool: string): ResultFamily {
  return FAMILY_BY_TOOL[tool] ?? 'generic';
}

export function toolLabel(tool: string): string {
  return TOOL_LABELS[tool] ?? humanize(tool);
}

export function familyLabel(family: ResultFamily): string {
  return FAMILY_LABELS[family];
}

function humanize(tool: string): string {
  return tool
    .replace(/[_-]+/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .trim();
}

/* ─── Output kind ───────────────────────────────────────────────────────────── */

export function outputKindFor(
  family: ResultFamily,
  result: Record<string, any> | null | undefined,
  geojsonRef: string | null | undefined,
): OutputKind {
  if (result?.image || result?.type === 'heatmap_raster') return 'image';
  // A session ref means a vector layer is mounted — prefer that over the family
  // default so vector-producing tools (e.g. zonal_stats) are never mislabeled raster.
  if (geojsonRef) return 'vector';
  if (family === 'raster' || family === 'remote_sensing') return 'raster';
  if (family === 'spatial_stats') return 'statistic';
  if (family === 'cartography') return 'none';
  return result?.data ? 'vector' : 'statistic';
}

/* ─── Metric extraction (scalars only) ──────────────────────────────────────── */

/** Safely read a (possibly nested) scalar path from the slim_event. */
function readPath(src: Record<string, any> | null | undefined, path: string): any {
  if (!src) return undefined;
  const parts = path.split('.');
  let cur: any = src;
  for (const p of parts) {
    if (cur == null || typeof cur !== 'object') return undefined;
    cur = cur[p];
  }
  return cur;
}

function num(v: any): number | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined;
}

function round(n: number, digits = 3): number {
  const f = 10 ** digits;
  return Math.round(n * f) / f;
}

function metric(label: string, v: number | undefined, opts: { unit?: string; digits?: number; emphasis?: 'primary' | 'secondary' } = {}): ResultMetric | null {
  if (v === undefined) return null;
  return {
    label,
    value: opts.digits ? round(v, opts.digits) : v,
    unit: opts.unit,
    emphasis: opts.emphasis,
  };
}

/**
 * Extract result metrics from the slim_event. Reads only scalar fields; never
 * touches feature arrays. Returns [] when no scalar metrics are available
 * (feature count etc. is then sourced from the descriptor endpoint).
 */
export function extractMetrics(
  family: ResultFamily,
  step: StepResultEvent,
): ResultMetric[] {
  const r = step.result ?? {};
  const data = r.data;
  const out: (ResultMetric | null)[] = [];

  switch (family) {
    case 'spatial_stats': {
      if (step.tool === 'moran_i') {
        out.push(metric("Moran's I", num(readPath(data, 'moran_i')), { digits: 4, emphasis: 'primary' }));
        out.push(metric('期望 I', num(readPath(data, 'expected_i')), { digits: 4 }));
        out.push(metric('p 值', num(readPath(data, 'p_value')), { digits: 4 }));
        const pattern = readPath(data, 'pattern');
        if (typeof pattern === 'string') out.push({ label: '空间模式', value: pattern });
        out.push(metric('要素数', num(readPath(data, 'n_features'))));
      } else if (step.tool === 'standard_deviational_ellipse') {
        out.push(metric('椭圆面积', num(readPath(data, 'area_km2')), { unit: 'km²', digits: 2, emphasis: 'primary' }));
        out.push(metric('方向角', num(readPath(data, 'angle_deg')), { unit: '°', digits: 1 }));
        const dir = readPath(data, 'direction');
        if (typeof dir === 'string') out.push({ label: '方向', value: dir });
      } else {
        out.push(metric('要素数', num(readPath(data, 'count')), { emphasis: 'primary' }));
        out.push(metric('总面积', num(readPath(data, 'total_area_m2')), { unit: 'm²', digits: 1 }));
        out.push(metric('总长度', num(readPath(data, 'total_length_m')), { unit: 'm', digits: 1 }));
      }
      break;
    }
    case 'hotspot': {
      out.push(metric('热点数', num(readPath(data, 'hot_spots_count')), { emphasis: 'primary' }));
      out.push(metric('冷点数', num(readPath(data, 'cold_spots_count'))));
      out.push(metric('距离带', num(readPath(data, 'distance_band_m')), { unit: 'm', digits: 1 }));
      break;
    }
    case 'cluster': {
      const method = readPath(data, 'method');
      if (typeof method === 'string') out.push({ label: '方法', value: method });
      out.push(metric('簇数', num(readPath(data, 'n_clusters')), { emphasis: 'primary' }));
      const stats = readPath(data, 'cluster_stats');
      if (Array.isArray(stats)) out.push({ label: '簇统计', value: `${stats.length} 组` });
      break;
    }
    case 'density': {
      if (step.tool === 'heatmap_data') {
        out.push(metric('总点数', num(r.total_points ?? readPath(r, 'metadata.point_count')), { emphasis: 'primary' }));
      } else {
        out.push(metric('单元格数', num(readPath(data, 'count')), { emphasis: 'primary' }));
        out.push(metric('带宽', num(readPath(data, 'bandwidth_m')), { unit: 'm', digits: 1 }));
        const grid = readPath(data, 'grid_size');
        if (Array.isArray(grid)) out.push({ label: '网格尺寸', value: `${grid[0]}×${grid[1]}` });
        out.push(metric('最小密度', num(readPath(data, 'stats.min')), { digits: 4 }));
        out.push(metric('最大密度', num(readPath(data, 'stats.max')), { digits: 4 }));
        out.push(metric('平均密度', num(readPath(data, 'stats.mean_density')), { digits: 4 }));
      }
      break;
    }
    case 'network': {
      // isochrone: unreachable count is prose in summary (parsed as warning);
      // facility count comes from args. Nearest facility has no aggregate scalar.
      break;
    }
    case 'remote_sensing': {
      if (step.tool === 'compute_ndvi') {
        out.push(metric('NDVI 最小', num(readPath(r, 'ndvi_stats.min')), { digits: 3 }));
        out.push(metric('NDVI 最大', num(readPath(r, 'ndvi_stats.max')), { digits: 3, emphasis: 'primary' }));
        out.push(metric('NDVI 均值', num(readPath(r, 'ndvi_stats.mean')), { digits: 3 }));
        const cov = readPath(r, 'vegetation_coverage');
        if (typeof cov === 'number') out.push(metric('植被覆盖度', cov, { unit: '%', digits: 1, emphasis: 'primary' }));
        else if (typeof cov === 'string') out.push({ label: '植被覆盖度', value: cov });
      }
      break;
    }
    default:
      // buffer / overlay / interpolation / raster / h3 / cartography / generic:
      // no reliable scalar metrics in the slim_event; counts come from descriptor.
      break;
  }

  return out.filter((m): m is ResultMetric => m !== null);
}
