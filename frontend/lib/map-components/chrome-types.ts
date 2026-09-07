/**
 * Live/Export 共享的 chrome 词表（Wave 9/Review R1 单一来源）。
 *
 * - VISUAL_TYPES：export 侧「可视组件」词表（fromSpec 门 + chrome 路径切换）；
 * - CHROME_RENDERABLE_TYPES：live 侧 MapSpecChrome 挂载判定词表。
 * 两者必须满足 VISUAL_TYPES ⊆ CHROME_RENDERABLE_TYPES —— 否则出现
 * 「导出画得出、live 画不出来」的反向 parity 缺口（披露族/table_panel
 * 曾缺行）。本模块由两侧共同导入，export-chrome.parity.test.ts 锁定包含
 * 关系；新增类型时同表添加。
 */
export const CHROME_RENDERABLE_TYPES: ReadonlySet<string> = new Set([
  'title', 'subtitle', 'north_arrow', 'scale_bar', 'attribution',
  'continuous_colorbar', 'legend', 'categorical_legend',
  'annotation', 'statistics_panel', 'chart_panel', 'map_border', 'graticule',
  'inset_map',
  // VNext 披露族 + V4 表格面板（与 export 侧同表 —— 反向 parity）。
  'methodology_note', 'uncertainty_panel', 'decision_panel', 'table_panel',
]);

/** 装饰族（title/north_arrow 等制图装饰）—— MapDecorations 的让位门。 */
export function specHasDecorationComponent(
  enabledTypes: ReadonlySet<string>,
): boolean {
  const DECORATION_FAMILY: ReadonlySet<string> = new Set([
    'title', 'subtitle', 'north_arrow', 'scale_bar', 'attribution',
    'continuous_colorbar', 'legend', 'categorical_legend', 'graticule',
    'map_border', 'inset_map',
  ]);
  for (const t of enabledTypes) {
    if (DECORATION_FAMILY.has(t)) return true;
  }
  return false;
}
