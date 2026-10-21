/**
 * heatmap 型表达层的安全 paint —— live runtime 与 headless 编译器共用。
 *
 * 只保留 heatmap-* 键（circle 层改写为 heatmap 时 circle-* 键会被
 * MapLibre 拒绝），半径缺省走符号律（与 headless compiler 同一默认表）。
 *
 * #1747：此前 live（runtime.ts）与 headless（compiler.ts 表达窗子层）各写
 * 一份，且 headless 侧只发 `heatmap-radius` —— 带原生 heatmap paint
 * （heatmap-color 密度色带 / intensity / weight / opacity）的层，其热力
 * 色带在导出里掉回 MapLibre 默认蓝色色带。收口为单一实现后双路径不再
 * 漂移（导出 paint 与 live 逐键一致）。
 *
 * 注意：本文件被 headless CLI（jiti，无 vite 别名解析）导入 —— 只能写
 * 相对导入（与 compiler.ts 同纪律，见该文件顶部注记）。
 */

import { heatmapRadiusExpression } from "../map-kit/symbol-law";
import {
  asPaintValue,
  type MapLibrePaint,
  type MapLibrePaintValue,
} from "../carto-ir/style-abi";

export function heatmapOnlyPaint(
  nativePaint: Record<string, unknown>,
  featureCount: number | undefined,
): MapLibrePaint {
  const heat: MapLibrePaint = {};
  for (const [k, v] of Object.entries(nativePaint)) {
    if (k.startsWith("heatmap-")) heat[k] = v as MapLibrePaintValue;
  }
  if (heat["heatmap-radius"] === undefined) {
    heat["heatmap-radius"] = asPaintValue(heatmapRadiusExpression({ featureCount }));
  }
  return heat;
}
