/**
 * DataPlaneLoadPlan — viewport-aware 数据面装载规划（extreme-scale runtime v2）。
 *
 * 纯函数 planner：给定图层投影 + 当前视口/缩放，输出每层确定性装载决策
 * （mode/priority/urgency/LOD band/估算字节）。**只规划显示通道的加载顺序
 * 与档位，绝不触碰分析事实** —— 分析侧（filter/export/属性表）依旧走
 * ``ensureLayerData`` 的按需全量通道，两者在数据语义上互不影响。
 *
 * 边界纪律：
 * - 与 MVT 决策同口径：``hasTileUrl && mvt_capable && feature_count > 阈值``
 *   （与 renderer.isMvtSourceId / layer-data.isMvtLayer 的 5000 同值契约）。
 * - bbox 未知的层保守视为视口内（不饿死未知范围层），reasonCode 披露。
 * - NaN/负 zoom fail-closed 到 overview 档，绝不传播 NaN。
 * - planner 只报告 overBudget；逐出执行权在 RefDataCache/预算账本。
 */

export type ViewportBBox = [number, number, number, number];

export type LoadMode = 'mvt' | 'inline' | 'hydrate' | 'deferred' | 'local';
export type Urgency = 'interactive' | 'normal' | 'idle';
export type LodBand = 'overview' | 'coarse' | 'full';

/** 与 data-tiers 的 TIER_INLINE_FEATURES / MVT 阈值同值契约（5000）。 */
export const DEFAULT_VECTOR_TILE_THRESHOLD = 5000;

/** descriptor 缺 estimated_bytes 时的确定性兜底估算（字节/要素，经验值）。 */
export const FALLBACK_BYTES_PER_FEATURE = 200;

/** LOD 显示档位边界（仅影响调度优先级/披露，不改数据）。 */
export const LOD_ZOOM_COARSE = 8;
export const LOD_ZOOM_FULL = 12;

export interface PlanLayerInput {
  layerId: string;
  visible: boolean;
  /** 显示通道已持有 inline FeatureCollection（store layer.source 已挂载数据）。 */
  hydrated?: boolean;
  refId?: string | null;
  /** 挂了 MVT 瓦片模板（``_tileUrl``）—— 蕴含 ref 支撑。 */
  hasTileUrl?: boolean;
  descriptor?: {
    feature_count?: number;
    mvt_capable?: boolean;
    estimated_bytes?: number;
    bbox?: [number, number, number, number] | null;
  } | null;
}

export interface PlanViewport {
  /** 视口 bounds（[w,s,e,n]）。缺省 = 视口未知 → 全部保守视为视口内
   *  （绝不饿死未知视口的层，只影响排序）。 */
  bounds?: ViewportBBox;
  zoom: number;
}

export interface LayerLoadDecision {
  layerId: string;
  mode: LoadMode;
  /** 与输出顺序单调一致的非负整数（band × 100）。 */
  priority: number;
  urgency: Urgency;
  /** 显示档位标注（display-only；分析事实不受影响）。 */
  lodBand: LodBand;
  estFeatures: number;
  estBytes: number;
  inViewport: boolean;
  /** 封闭词表决策理由（evidence-honest）。 */
  reasonCode: string;
}

export interface DataPlaneLoadPlan {
  /** 已按调度顺序排序（band 降序 → estFeatures 升序 → layerId 字典序）。 */
  decisions: LayerLoadDecision[];
  totalEstBytes: number;
  hydrateCount: number;
  overBudget: boolean;
}

export interface PlanOptions {
  budgetBytes?: number;
  vectorTileThreshold?: number;
  /** true 时把不可交互（不可见或视口外）的 hydrate 降为 deferred —— 默认
   * false 保持既有全量挂载语义，只加优先级排序（渐进但不丢层）。 */
  deferOffViewport?: boolean;
}

export function lodBandForZoom(zoom: number): LodBand {
  // 正数一路外推到可表示上限（+Infinity 语义 = 无限放大 → full）；
  // NaN/非正数 fail-closed 到 0（overview —— 最保守的加载档）。
  const z = typeof zoom === 'number' && zoom > 0
    ? Math.min(zoom, Number.MAX_VALUE)
    : 0;
  if (z >= LOD_ZOOM_FULL) return 'full';
  if (z >= LOD_ZOOM_COARSE) return 'coarse';
  return 'overview';
}

function bboxIntersectsInclusive(
  a: ViewportBBox,
  b: ViewportBBox,
): boolean {
  return a[0] <= b[2] && a[2] >= b[0] && a[1] <= b[3] && a[3] >= b[1];
}

function finiteNonNegative(v: unknown): v is number {
  return typeof v === 'number' && Number.isFinite(v) && v >= 0;
}

/** 调度 band：3 可见+视口内 → 2 可见+视口外 → 1 隐藏+视口内 → 0 隐藏+视口外。 */
function scheduleRank(visible: boolean, inViewport: boolean): number {
  if (visible && inViewport) return 3;
  if (visible) return 2;
  if (inViewport) return 1;
  return 0;
}

/**
 * 构建确定性装载计划。同输入恒同输出；无隐藏状态、无 I/O。
 */
export function buildLoadPlan(
  layers: readonly PlanLayerInput[],
  viewport: PlanViewport,
  opts: PlanOptions = {},
): DataPlaneLoadPlan {
  const threshold = opts.vectorTileThreshold ?? DEFAULT_VECTOR_TILE_THRESHOLD;
  const lodBand = lodBandForZoom(viewport?.zoom ?? 0);
  const vb = viewport?.bounds;

  const decisions: LayerLoadDecision[] = (layers ?? []).map((layer) => {
    const refBacked = !!(layer.refId || layer.hasTileUrl);
    const d = layer.descriptor ?? null;
    const featureCount = finiteNonNegative(d?.feature_count) ? (d!.feature_count as number) : 0;
    const knownBytes = finiteNonNegative(d?.estimated_bytes) && (d!.estimated_bytes as number) > 0;
    const estFeatures = featureCount;
    const estBytes = knownBytes
      ? (d!.estimated_bytes as number)
      : estFeatures > 0
        ? estFeatures * FALLBACK_BYTES_PER_FEATURE
        : 0;

    const bbox = d?.bbox ?? null;
    // bbox 未知的层保守视为视口内：宁可多拉一次，不饿死未知范围层。
    const inViewport =
      !vb || !bbox ? true : bboxIntersectsInclusive(bbox, vb);

    const isMvt =
      !!layer.hasTileUrl &&
      d?.mvt_capable === true &&
      featureCount > threshold;

    let mode: LoadMode;
    let urgency: Urgency;
    let reasonCode: string;

    if (!refBacked) {
      mode = 'local';
      urgency = 'idle';
      reasonCode = 'local:no-ref';
    } else if (isMvt) {
      mode = 'mvt';
      urgency = 'idle';
      reasonCode = 'mvt:large-ref';
    } else if (layer.hydrated) {
      mode = 'inline';
      urgency = 'idle';
      reasonCode = 'inline:hydrated';
    } else {
      const interactable = layer.visible && inViewport;
      if (opts.deferOffViewport && !interactable) {
        mode = 'deferred';
        urgency = 'idle';
        reasonCode = 'deferred:not-interactable';
      } else {
        mode = 'hydrate';
        urgency = layer.visible
          ? inViewport
            ? 'interactive'
            : 'normal'
          : 'idle';
        reasonCode = !layer.visible
          ? 'hydrate:hidden'
          : !bbox
            ? 'hydrate:unknown-extent'
            : inViewport
              ? 'hydrate:viewport-visible'
              : 'hydrate:off-viewport';
      }
    }

    return {
      layerId: layer.layerId,
      mode,
      priority: scheduleRank(layer.visible, inViewport) * 100,
      urgency,
      lodBand,
      estFeatures,
      estBytes,
      inViewport,
      reasonCode,
    };
  });

  decisions.sort((a, b) => {
    if (b.priority !== a.priority) return b.priority - a.priority;
    if (a.estFeatures !== b.estFeatures) return a.estFeatures - b.estFeatures;
    return a.layerId < b.layerId ? -1 : a.layerId > b.layerId ? 1 : 0;
  });

  const totalEstBytes = decisions.reduce((sum, d) => sum + d.estBytes, 0);
  const hydrateCount = decisions.reduce(
    (n, d) => (d.mode === 'hydrate' ? n + 1 : n),
    0,
  );

  return {
    decisions,
    totalEstBytes,
    hydrateCount,
    overBudget:
      typeof opts.budgetBytes === 'number'
        && Number.isFinite(opts.budgetBytes)
        ? totalEstBytes > opts.budgetBytes
        : false,
  };
}
