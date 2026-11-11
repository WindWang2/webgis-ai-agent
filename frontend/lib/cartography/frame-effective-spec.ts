/**
 * G25 帧有效文档前端解析器 —— 后端 `frame_products/resolver.py` 的 JS 等价
 * （渲染面），使浏览器交互画幅与服务端验证/出版读到**同一份**帧文档。
 *
 * 解析顺序（与后端逐位一致）：
 * 1. layerOverrides（layout.frames[id].layerOverrides 逐键覆写，id 不可覆写）；
 * 2. data.where（feature_pages where AST —— where-ast.ts 同源求值器）；
 * 3. data.time_window（temporal [start,end) 窗：数值域 float 比较，字符串域
 *    字典序；仅数值/字符串字段命中 —— 后端 _in_window 同语义）；
 * 4. shared_expression（共享分级 breaks/method/k/palette 写入绑定层
 *    legend_spec —— **同值同色、绝不重算**：运行时只搬运产品域的既有
 *    breaks，不在帧子集上重新分级；paint 由 MapSpecRuntime 的
 *    thematic-paint 从同一 legend_spec 确定性派生，图例/渲染不漂移）。
 *
 * 失败语义（typed，绝不静默回退整幅主文档）：
 * - frame_layer_unavailable / frame_source_not_inline / frame_where_invalid /
 *   frame_time_window_invalid → FrameEffectiveSpecError（帧 error 态）。
 *
 * 纯函数、输入不可变（触达路径浅拷贝 —— 后端同纪律）。
 */
import { filterFeaturesByWhere, WhereAstError, type WhereNode } from '@/lib/cartography/where-ast';
import type { MapSpec } from '@/lib/mapspec-compiler/types';

export class FrameEffectiveSpecError extends Error {
  constructor(
    public code:
      | 'frame_not_found'
      | 'frame_layer_unavailable'
      | 'frame_source_not_inline'
      | 'frame_where_invalid'
      | 'frame_time_window_invalid',
    message: string,
  ) {
    super(message);
    this.name = 'FrameEffectiveSpecError';
  }
}

export interface FrameEffectiveSpecResult {
  frameId: string;
  document: MapSpec;
  /** 生效的解析步骤（ordered，与后端 applied 同词表）。 */
  applied: Array<'layer_overrides' | 'where_filter' | 'time_window' | 'shared_expression'>;
  /** where/time_window 过滤后绑定层剩余要素数（无过滤步骤 = null）。 */
  filteredFeatureCount: number | null;
  disclosures: string[];
}

interface FrameSlotRaw {
  frame_id?: string;
  data?: {
    ref?: string;
    layer_id?: string;
    field?: string;
    content_revision?: number;
    where?: WhereNode;
    time_window?: Record<string, unknown>;
  };
}

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object';
}

/** 步骤 1：layerOverrides 逐键覆写（id 不可覆写；触达路径浅拷贝）。 */
export function applyFrameLayerOverrides(
  doc: MapSpec,
  frame: { layerOverrides?: Record<string, Record<string, unknown>> } | null | undefined,
): MapSpec {
  const overrides = frame?.layerOverrides;
  if (!isRecord(overrides) || Object.keys(overrides).length === 0) return doc;
  const out: Record<string, unknown> = { ...(doc as unknown as Record<string, unknown>) };
  const layers = Array.isArray(doc.layers) ? [...doc.layers] : [];
  out.layers = layers.map((layer) => {
    if (!isRecord(layer)) return layer;
    const ov = overrides[String(layer.id)];
    if (!isRecord(ov)) return layer;
    const merged: Record<string, unknown> = { ...(layer as unknown as Record<string, unknown>) };
    for (const [k, v] of Object.entries(ov)) {
      if (k !== 'id') merged[k] = v; // id 不允许覆写（后端同纪律）
    }
    return merged as unknown as (typeof layers)[number];
  });
  return out as unknown as MapSpec;
}

/** 绑定层 → 其 source 条目（后端 _layer_source 同构）。 */
function boundLayerSource(
  doc: MapSpec,
  layerId: string | null,
): { layer: Record<string, unknown>; sourceKey: string; source: Record<string, unknown> } | null {
  const layers = Array.isArray(doc.layers) ? doc.layers : [];
  for (const layer of layers) {
    if (!isRecord(layer)) continue;
    const lid = String(layer.id ?? '');
    if (layerId ? lid === layerId : !!lid) {
      const sid = layer.source;
      if (typeof sid !== 'string') return null;
      const sources = isRecord(doc.sources) ? doc.sources : null;
      const src = sources?.[sid];
      if (isRecord(src)) return { layer, sourceKey: sid, source: src };
      return null;
    }
  }
  return null;
}

interface InlinePayload {
  source: Record<string, unknown>;
  payloadKey: 'inlineData' | 'data';
  features: Array<Record<string, unknown>>;
}

function inlineFeaturesOf(source: Record<string, unknown>): InlinePayload | null {
  let payloadKey: 'inlineData' | 'data' = 'inlineData';
  let payload = source.inlineData;
  if (!isRecord(payload)) {
    payloadKey = 'data';
    payload = source.data;
  }
  if (!isRecord(payload) || !Array.isArray(payload.features)) return null;
  return {
    source,
    payloadKey,
    features: payload.features.filter((f) => isRecord(f)) as Array<Record<string, unknown>>,
  };
}

/** 触达路径浅拷贝地替换绑定源 features。 */
function withFilteredSource(
  doc: MapSpec,
  sourceKey: string,
  kept: Array<Record<string, unknown>>,
): MapSpec {
  const docRec = doc as unknown as Record<string, unknown>;
  const out: Record<string, unknown> = { ...docRec };
  const sources = isRecord(docRec.sources) ? { ...docRec.sources } : {};
  const rawSource = sources[sourceKey];
  const src: Record<string, unknown> = isRecord(rawSource) ? { ...rawSource } : {};
  let payloadKey: 'inlineData' | 'data' = 'inlineData';
  let payload = src.inlineData;
  if (!isRecord(payload)) {
    payloadKey = 'data';
    payload = src.data;
  }
  src[payloadKey] = { ...(isRecord(payload) ? payload : {}), features: kept };
  sources[sourceKey] = src;
  out.sources = sources;
  return out as unknown as MapSpec;
}

/** 步骤 3：time_window [start,end) 窗（后端 _in_window 同语义）。 */
function timeWindowPredicate(
  tw: Record<string, unknown>,
): (value: unknown) => boolean {
  const lo = tw.start;
  const hi = tw.end;
  if (lo === undefined || hi === undefined) {
    throw new FrameEffectiveSpecError('frame_time_window_invalid', 'time_window requires start/end');
  }
  const numeric = typeof lo === 'number' && !Number.isNaN(lo);
  if (numeric) {
    if (typeof hi !== 'number' || Number.isNaN(hi)) {
      throw new FrameEffectiveSpecError('frame_time_window_invalid', 'time_window bounds must share type');
    }
    return (value) =>
      typeof value === 'number' && !Number.isNaN(value) && (lo as number) <= value && value < (hi as number);
  }
  if (typeof lo !== 'string' || typeof hi !== 'string') {
    throw new FrameEffectiveSpecError('frame_time_window_invalid', 'time_window bounds must be numeric or string');
  }
  return (value) => typeof value === 'string' && lo <= value && value < hi;
}

/**
 * 解析一帧的有效文档（前端渲染面）。composingSpec 建议是
 * composeLiveMapSpec + injectResolvedRefSources 之后、已含内联数据的
 * live spec（ref 未水合 → typed error，绝不渲染全量主文档冒充帧）。
 */
export function resolveFrameEffectiveSpec(
  composingSpec: MapSpec,
  /** frame_product 配置（FrameProductConfig 或等价 JSON —— 内部自行窄化）。 */
  product: unknown,
  frameId: string,
): FrameEffectiveSpecResult {
  const productRec = isRecord(product) ? product : null;
  const framesAny = productRec ? productRec.frames : null;
  const slots = (Array.isArray(framesAny) ? framesAny : []) as Array<Record<string, unknown>>;
  const slotRaw = slots.find((s) => isRecord(s) && s.frame_id === frameId) as FrameSlotRaw | undefined;
  if (!slotRaw) {
    throw new FrameEffectiveSpecError('frame_not_found', `frame ${frameId} not in product`);
  }
  const doc0 = composingSpec;
  // 步骤 1：layerOverrides。
  const layoutFrame = findLayoutFrame(doc0, frameId);
  const doc1 = applyFrameLayerOverrides(doc0, layoutFrame as never);
  const applied: FrameEffectiveSpecResult['applied'] = [];
  if (doc1 !== doc0) applied.push('layer_overrides');

  // 步骤 2/3：where / time_window（绑定层内联要素过滤）。
  const data = slotRaw.data;
  let doc = doc1;
  let filteredFeatureCount: number | null = null;
  if (data) {
    const layerId = typeof data.layer_id === 'string' ? data.layer_id : null;
    const bound = boundLayerSource(doc, layerId);
    if (!bound) {
      throw new FrameEffectiveSpecError(
        'frame_layer_unavailable',
        `绑定层 ${String(layerId)} 不存在或无数据源`,
      );
    }
    const inline = inlineFeaturesOf(bound.source);
    if (!inline) {
      throw new FrameEffectiveSpecError(
        'frame_source_not_inline',
        `层 ${String(layerId)} 的源不是内联 FeatureCollection（ref 源先水合）`,
      );
    }
    let kept = inline.features;
    if (data.where) {
      try {
        kept = filterFeaturesByWhere(kept, data.where);
        applied.push('where_filter');
      } catch (e) {
        if (e instanceof WhereAstError) {
          throw new FrameEffectiveSpecError('frame_where_invalid', `where AST 非法：${e.message}`);
        }
        throw e;
      }
    }
    if (data.time_window && isRecord(data.time_window)) {
      const tw = data.time_window;
      const field = typeof tw.time_field === 'string' ? tw.time_field : '';
      if (!field) {
        throw new FrameEffectiveSpecError('frame_time_window_invalid', 'time_window requires time_field');
      }
      const predicate = timeWindowPredicate(tw);
      kept = kept.filter((f) => {
        const props = isRecord(f.properties) ? f.properties : {};
        return predicate(props[field]);
      });
      applied.push('time_window');
    }
    filteredFeatureCount = kept.length;
    if (kept.length !== inline.features.length) {
      doc = withFilteredSource(doc, bound.sourceKey, kept);
    }
  }

  // 步骤 4：共享分级写入绑定层 legend_spec（同值同色，不重算）。
  const shared = productRec ? productRec.shared_expression : null;
  const disclosures: string[] = [];
  if (isRecord(shared) && Array.isArray(shared.breaks) && (shared.breaks as unknown[]).length >= 2) {
    const breaks = (shared.breaks as unknown[]).map((b) => Number(b));
    const boundLayerId = typeof data?.layer_id === 'string' ? data.layer_id : null;
    if (boundLayerId) {
      const docRec = doc as unknown as Record<string, unknown>;
      const layers = Array.isArray(docRec.layers) ? [...docRec.layers] : [];
      let hit = false;
      const newLayers = layers.map((layer) => {
        if (!isRecord(layer) || String(layer.id ?? '') !== boundLayerId) return layer;
        const legend = isRecord(layer.legend_spec) ? { ...layer.legend_spec } : null;
        if (!legend) return layer;
        legend.breaks = breaks;
        if (typeof shared.method === 'string' && shared.method) legend.method = shared.method;
        legend.k = breaks.length - 1;
        if (typeof shared.palette === 'string' && shared.palette) legend.palette = shared.palette;
        legend.shared_scope = true; // 同域分级标记（渲染/图例可识别）
        hit = true;
        return { ...layer, legend_spec: legend } as unknown as (typeof layers)[number];
      });
      if (hit) {
        const docRec = doc as unknown as Record<string, unknown>;
        doc = { ...docRec, layers: newLayers } as unknown as MapSpec; // 拷贝不变异
        applied.push('shared_expression');
      } else {
        disclosures.push('shared_expression_layer_without_legend');
      }
    }
  } else if (isRecord(shared)) {
    disclosures.push('shared_expression_not_computed');
  }

  return { frameId, document: doc, applied, filteredFeatureCount, disclosures };
}

function findLayoutFrame(
  spec: MapSpec,
  frameId: string,
): { layerOverrides?: Record<string, Record<string, unknown>> } | null {
  const frames = spec?.layout?.frames;
  if (!Array.isArray(frames)) return null;
  for (const f of frames) {
    if (isRecord(f) && f.id === frameId) return f as { layerOverrides?: Record<string, Record<string, unknown>> };
  }
  return null;
}
