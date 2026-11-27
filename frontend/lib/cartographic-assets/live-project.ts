/**
 * G40 live 投影：MapSpec symbol_asset 绑定 → MapLibre 层定义补丁。
 *
 * 语义合同（与导出侧 mapspec_to_svg.py 同源）：
 * - point_symbol：层型投影为 symbol；icon-size = size_px / max(vbW,vbH)
 *   （icon-size 是相对注册图像内在尺寸的倍率；注册图像已按 size_px×ratio
 *   光栅化 → icon-size=1 为标准尺寸，此处保持 1 由图像自身承载尺寸——
 *   锚点/旋转由 icon-anchor/icon-rotate 承载）；
 * - 碰撞合同（S31）：icon-allow-overlap=true + icon-ignore-placement=true
 *   （默认不因碰撞静默省略数据图标；如实重叠）。declutter 未实现 →
 *   显式拒绝（UNSUPPORTED_DECLUTTER）；
 * - area_texture：fill-pattern 引用注册图像；fill-color 移除（MapLibre
 *   fill-pattern 下 fill-color 失效；背景色烘焙进 tile，S13）；
 * - 类别映射：点符号 icon-image 用 match 表达式（typed key，S02 不并类）；
 *   面纹理 fill-pattern 恒定 → 拆过滤子层（有界 ≤16，超出披露）。
 */

import {
  CategoryBinding,
  PointParams,
  SymbolAssetBinding,
  SymbolAssetDocument,
  SymbolAssetParamError,
  TextureParams,
  normalizePointParams,
  normalizeTextureParams,
  pointImageId,
} from './contracts';
import { ensureRegistered, RegistrationFailure } from './register';
import type { Map as MapLibreMap } from 'maplibre-gl';

export const MAX_TEXTURE_CATEGORY_SUBLAYERS = 16;

export interface LayerPatch {
  /** 投影后的 MapLibre 层型（point_symbol → 'symbol'） */
  type?: 'symbol';
  /** 写入 layout 的键（icon-*） */
  layout: Record<string, unknown>;
  /** 写入 paint 的键（fill-pattern 等） */
  paintOps: { set: Record<string, unknown>; remove: string[] };
  /** 类别面纹理的过滤子层（texture only） */
  categorySublayers?: Array<{
    suffix: string;
    filter: unknown;
    imageId: string;
  }>;
  /** 主层附加 filter（类别纹理：排除已映射键；与既有 filter 以 all 组合） */
  mainFilterExtra?: unknown;
  /** 类别规则超帽披露 */
  disclosures: string[];
}

export interface ProjectionFailure {
  code: string;
  message: string;
}

/** 解析要素级绑定（类别 typed 匹配；不可解析 → null = 该要素不画）。 */
export function resolveBindingForFeature(
  binding: SymbolAssetBinding,
  props: Record<string, unknown> | undefined,
  docs: Map<string, SymbolAssetDocument>,
): { doc: SymbolAssetDocument; params: Record<string, unknown> } | null {
  const mainDoc = docs.get(binding.content_digest);
  const mainParams = (binding.params ?? {}) as Record<string, unknown>;
  const cb = binding.category_binding;
  if (!cb) return mainDoc ? { doc: mainDoc, params: mainParams } : null;
  const raw = (props ?? {})[cb.field];
  let matched: { content_digest: string; params?: Record<string, unknown> } | null = null;
  // 类型不符的值（如 "1" 对 number 键，S02）不并入类别匹配 —— matched 保持
  // null，直接落 other_mode/mainDoc 回退。
  if (cb.key_type === 'number') {
    if (typeof raw === 'number' && Number.isFinite(raw)) {
      matched = (cb.map.find(
        (r) => typeof r.key === 'number' && !Number.isNaN(r.key)
          && r.key === raw) ?? null);
    }
  } else if (cb.key_type === 'bool') {
    if (typeof raw === 'boolean') {
      matched = (cb.map.find((r) => typeof r.key === 'boolean' && r.key === raw) ?? null);
    }
  } else {
    if (typeof raw === 'string') {
      matched = (cb.map.find((r) => typeof r.key === 'string' && r.key === raw) ?? null);
    }
  }
  if (matched) {
    const doc = docs.get(matched.content_digest);
    return doc
      ? { doc, params: matched.params ?? mainParams }
      : null;
  }
  if (cb.other_mode === 'fallback_asset' && cb.other_content_digest) {
    const doc = docs.get(cb.other_content_digest);
    return doc
      ? { doc, params: (cb.other_params as Record<string, unknown>) ?? mainParams }
      : null;
  }
  return mainDoc ? { doc: mainDoc, params: mainParams } : null;
}

/**
 * 类别映射 → icon-image match 表达式（点符号；typed 字面量）。
 *
 * params 必须是归一化形（normalizePointParams 输出）：imageId 与
 * ensureRegistered 的注册名同源（register.ts:103 亦以归一化 params 算
 * id）。喂原始 params 时，省略 params / 键序不同 → stableKey 分歧 →
 * 表达式指向未注册图像，类别图标静默不渲染。
 */
export function categoryIconExpression(
  cb: CategoryBinding,
  binding: SymbolAssetBinding,
): unknown {
  const arms: unknown[] = [];
  for (const rule of cb.map) {
    arms.push(rule.key, pointImageId(rule.content_digest, (rule.params ?? {}) as Record<string, unknown>));
  }
  const fallback = cb.other_mode === 'fallback_asset' && cb.other_content_digest
    ? pointImageId(cb.other_content_digest, (cb.other_params ?? {}) as Record<string, unknown>)
    : pointImageId(binding.content_digest, (binding.params ?? {}) as Record<string, unknown>);
  return ['match', ['get', cb.field], ...arms, fallback];
}

/**
 * 绑定 → 层补丁（注册图像经 ensureRegistered；map 缺席/注册失败 →
 * ProjectionFailure，调用方跳层并披露 —— 不默认圆伪装，S23）。
 */
export function projectSymbolAssetLayer(
  map: MapLibreMap | null | undefined,
  binding: SymbolAssetBinding & { declutter?: unknown },
  docs: Map<string, SymbolAssetDocument>,
): { patch: LayerPatch; failures: RegistrationFailure[] } | { failure: ProjectionFailure } {
  if (binding.declutter !== undefined && binding.declutter !== false) {
    return {
      failure: {
        code: 'UNSUPPORTED_DECLUTTER',
        message: 'declutter 组合未实现（默认 icon-allow-overlap=true 如实重叠）',
      },
    };
  }
  const doc = docs.get(binding.content_digest);
  if (!doc || doc.content_digest !== binding.content_digest) {
    return {
      failure: {
        code: 'symbol_asset_unavailable',
        message: `资产内容缺席: ${binding.asset_id}@${binding.revision}`,
      },
    };
  }
  const failures: RegistrationFailure[] = [];
  const disclosures: string[] = [];
  if (binding.kind === 'point_symbol') {
    let params: PointParams;
    try {
      params = normalizePointParams(
        (binding.params ?? {}) as Record<string, unknown>, doc);
    } catch (err) {
      if (!(err instanceof SymbolAssetParamError)) throw err;
      return { failure: { code: err.code, message: err.message } };
    }
    const main = ensureRegistered(map, doc, params as unknown as Record<string, unknown>, binding.content_digest);
    if ('code' in main) {
      return {
        failure: {
          code: main.code,
          message: `主绑定注册失败: ${main.message}`,
        },
      };
    }
    // 类别规则资产的图像也需注册（缺一披露，不整层失败）
    const cb = binding.category_binding;
    let iconImage: unknown = main.imageId;
    if (cb) {
      for (const rule of cb.map) {
        const rdoc = docs.get(rule.content_digest);
        if (!rdoc) {
          failures.push({
            code: 'ASSET_DOC_INVALID',
            message: `类别规则资产缺席: ${String(rule.key)}`,
            imageId: rule.asset_id,
          });
          continue;
        }
        const r = ensureRegistered(
          map, rdoc, (rule.params ?? {}) as Record<string, unknown>,
          rule.content_digest);
        if ('code' in r) failures.push(r);
      }
      if (cb.other_mode === 'fallback_asset' && cb.other_content_digest) {
        const odoc = docs.get(cb.other_content_digest);
        if (odoc) {
          const r = ensureRegistered(
            map, odoc, (cb.other_params ?? {}) as Record<string, unknown>,
            cb.other_content_digest);
          if ('code' in r) failures.push(r);
        }
      }
      // 表达式侧与注册侧同源：ensureRegistered（register.ts:103）以归一化
      // params 算 imageId；若把原始 params 喂给 categoryIconExpression，
      // 类别规则省略 params 或键序不同 → stableKey 分歧 → 表达式指向未注册
      // 图像，类别图标静默不渲染。归一化幂等且键序规范化，预归一化后再交
      // 表达式（归一化失败 = 注册亦失败已披露，保留原 params）。
      const exprParams = (
        raw: Record<string, unknown> | undefined,
        digest: string | undefined,
      ): Record<string, unknown> => {
        const d = digest ? docs.get(digest) : undefined;
        if (!d) return (raw ?? {}) as Record<string, unknown>;
        try {
          return normalizePointParams(
            raw ?? {}, d) as unknown as Record<string, unknown>;
        } catch {
          return (raw ?? {}) as Record<string, unknown>;
        }
      };
      iconImage = categoryIconExpression(
        {
          ...cb,
          map: cb.map.map((rule) => ({
            ...rule,
            params: exprParams(rule.params, rule.content_digest),
          })),
          other_params: exprParams(cb.other_params, cb.other_content_digest),
        },
        { ...binding, params: params as unknown as Record<string, unknown> },
      );
    }
    return {
      patch: {
        type: 'symbol',
        layout: {
          'icon-image': iconImage,
          'icon-anchor': params.anchor,
          'icon-rotate': params.rotation_deg,
          'icon-allow-overlap': true,
          'icon-ignore-placement': true,
          // 与 MapLibre 默认一致显式声明：viewport 对齐（合同披露面）
          'icon-rotation-alignment': 'viewport',
        },
        paintOps: {
          set: params.opacity < 1 ? { 'icon-opacity': params.opacity } : {},
          remove: ['circle-color', 'circle-radius', 'circle-opacity',
                   'circle-stroke-color', 'circle-stroke-width'],
        },
        disclosures,
      },
      failures,
    };
  }
  // area_texture
  let params: TextureParams;
  try {
    params = normalizeTextureParams(
      (binding.params ?? {}) as Record<string, unknown>, doc);
  } catch (err) {
    if (!(err instanceof SymbolAssetParamError)) throw err;
    return { failure: { code: err.code, message: err.message } };
  }
  const main = ensureRegistered(map, doc, params as unknown as Record<string, unknown>, binding.content_digest);
  if ('code' in main) {
    return {
      failure: {
        code: main.code,
        message: `主绑定注册失败: ${main.message}`,
      },
    };
  }
  const cb = binding.category_binding;
  const categorySublayers: Array<{ suffix: string; filter: unknown; imageId: string }> = [];
  if (cb) {
    const rules = cb.map.slice(0, MAX_TEXTURE_CATEGORY_SUBLAYERS);
    if (cb.map.length > MAX_TEXTURE_CATEGORY_SUBLAYERS) {
      disclosures.push(
        `类别纹理规则 ${cb.map.length} 条超子层帽 ${MAX_TEXTURE_CATEGORY_SUBLAYERS}，超出部分不渲染（披露）`);
    }
    for (const rule of rules) {
      const rdoc = docs.get(rule.content_digest);
      if (!rdoc) {
        failures.push({
          code: 'ASSET_DOC_INVALID',
          message: `类别规则资产缺席: ${String(rule.key)}`,
        });
        continue;
      }
      const r = ensureRegistered(
        map, rdoc, (rule.params ?? {}) as Record<string, unknown>, rule.content_digest);
      if ('code' in r) {
        failures.push(r);
        continue;
      }
      categorySublayers.push({
        suffix: `__texcat_${String(rule.key).replace(/[^a-zA-Z0-9_-]/g, '_')}`,
        filter: ['==', ['get', cb.field], rule.key],
        imageId: r.imageId,
      });
    }
  }
  // 未匹配要素的主层纹理：keep_current=主绑定；fallback_asset=other。
  // 主层 filter 需排除已映射键（同要素不叠两种纹理）。
  let mainPatternId = main.imageId;
  let mainExclusion: unknown = undefined;
  if (cb && cb.map.length > 0) {
    const keys = cb.map.map((r) => r.key);
    mainExclusion = ['!', ['in', ['get', cb.field], ['literal', keys]]];
    if (cb.other_mode === 'fallback_asset' && cb.other_content_digest) {
      const odoc = docs.get(cb.other_content_digest);
      if (odoc) {
        const r = ensureRegistered(
          map, odoc, (cb.other_params ?? {}) as Record<string, unknown>,
          cb.other_content_digest);
        if ('code' in r) failures.push(r);
        else mainPatternId = r.imageId;
      } else {
        failures.push({
          code: 'ASSET_DOC_INVALID',
          message: 'other 资产缺席（未匹配要素回落主绑定纹理）',
        });
      }
    }
  }
  return {
    patch: {
      layout: {},
      paintOps: {
        set: { 'fill-pattern': mainPatternId },
        remove: ['fill-color', 'fill-translate', 'fill-translate-anchor'],
      },
      mainFilterExtra: mainExclusion,
      categorySublayers: cb ? categorySublayers : undefined,
      disclosures,
    },
    failures,
  };
}
