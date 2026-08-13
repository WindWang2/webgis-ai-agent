/**
 * Derive safe "next actions" from a result's capabilities (spec §13).
 *
 * Actions never duplicate tool execution logic — map actions (show/hide/zoom) go
 * through the store, and analytical suggestions (buffer/overlay/classify/export)
 * are surfaced as chat *intents* the user can send, reusing existing workflows.
 */
import type { OutputDescriptor, ResultFamily, SuggestedAction } from './types';

export function deriveSuggestedActions(
  family: ResultFamily,
  outputs: OutputDescriptor[],
  /** Whether the result currently has a visible bound layer. */
  hasVisibleLayer: boolean,
  /** Whether any bound layer exists (visible or hidden). */
  hasBoundLayer: boolean,
): SuggestedAction[] {
  const actions: SuggestedAction[] = [];
  const primary = outputs[0];
  const kind = primary?.kind;

  // Map actions — only when a layer is actually bound.
  if (hasBoundLayer) {
    actions.push({
      kind: hasVisibleLayer ? 'hide' : 'show_on_map',
      label: hasVisibleLayer ? '在地图上隐藏' : '在地图上显示',
      available: true,
    });
    actions.push({ kind: 'zoom', label: '缩放至结果', available: !!primary?.bbox || hasBoundLayer });
  }

  // Capability-driven analytical suggestions (chat intents).
  if (kind === 'vector' || kind === 'image') {
    actions.push({ kind: 'style', label: '调整样式', available: hasBoundLayer });
    if (family === 'hotspot' || family === 'cluster' || family === 'h3') {
      actions.push({ kind: 'inspect', label: '检查显著要素', available: true });
    }
  }
  if (kind === 'vector') {
    actions.push({ kind: 'buffer', label: '对结果做缓冲区', available: !!primary?.ref });
    actions.push({ kind: 'overlay', label: '与其他图层叠加', available: !!primary?.ref });
  }
  if (kind === 'raster') {
    actions.push({ kind: 'classify', label: '分类', available: true });
  }
  if (primary) {
    actions.push({ kind: 'export', label: '导出结果', available: true });
  }

  return actions;
}
