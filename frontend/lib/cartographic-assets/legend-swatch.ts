/**
 * G40 图例 swatch 解析：绑定块 → 内联 SVG 片段（屏上图例消费）。
 *
 * 语义与后端 legend.py `_swatch_of` 同源：builtin 文档由列表端点内联 /
 * doc 缓存；DB 资产经 digest 缓存取。缺席 → null（渲染端回退纯色块，
 * 不伪装）。类别绑定按 entry key 匹配规则资产。
 */

import {
  SymbolAssetBinding,
  SymbolAssetDocument,
} from './contracts';
import { swatchSvgString } from './rasterize';
import { cachedDoc } from './api';

/** 绑定（含类别规则）→ 16×13 盒内联 SVG；doc 缺席 → null。 */
export function resolveBindingSwatch(
  binding: SymbolAssetBinding & { category_binding?: SymbolAssetBinding['category_binding'] },
  categoryKey?: string | number | boolean,
): string | null {
  let digest = binding.content_digest;
  let params: Record<string, unknown> = (binding.params ?? {}) as Record<string, unknown>;
  const cb = binding.category_binding;
  if (cb && categoryKey !== undefined) {
    const rule = cb.map.find((r) => r.key === categoryKey);
    if (rule) {
      digest = rule.content_digest;
      params = (rule.params ?? params) as Record<string, unknown>;
    } else if (cb.other_mode === 'fallback_asset' && cb.other_content_digest) {
      digest = cb.other_content_digest;
      params = (cb.other_params ?? params) as Record<string, unknown>;
    }
  }
  const doc: SymbolAssetDocument | null = cachedDoc(digest);
  if (!doc) return null;
  try {
    return swatchSvgString(doc, params, 16, 13);
  } catch {
    return null;
  }
}
