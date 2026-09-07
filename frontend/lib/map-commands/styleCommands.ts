import type { CommandEntry, MapCommandResult } from './types';
import { applyStyleIntent, STYLE_PALETTES, CLASSIFICATION_METHODS, type StyleIntent } from '@/lib/styles/style-intent';

/**
 * apply_style_intent —— agent 的 typed 样式意图命令（Workbench V4 / Wave 7）。
 *
 * 「颜色浅一点 / 分成 7 级 / 边界线细一些」类语义请求由后端/agent 解析为
 * 本词表的封闭意图，经 MapAction 队列确定性落地：applier 校验 + 钳制 +
 * 相对意图按当前样式求值；非法意图 failed（附原因），锁定层 failed
 * （lock 护栏与批量操作一致）。
 */

function validIntent(p: Record<string, unknown>): boolean {
  if (typeof p['layerId'] !== 'string' || !p['layerId']) return false;
  const kind = p['kind'];
  if (typeof kind !== 'string') return false;
  switch (kind) {
    case 'set_color':
      return typeof p['color'] === 'string' && /^#[0-9a-fA-F]{6}$/.test(p['color']);
    case 'set_palette':
      return typeof p['palette'] === 'string' && STYLE_PALETTES.has(p['palette']);
    case 'set_opacity':
    case 'set_stroke_width':
    case 'set_point_size':
    case 'lighten':
    case 'darken':
    case 'thinner':
    case 'thicker':
      return true; // 数值合法性由 applier 钳制（amount 缺省语义）
    case 'set_classification':
      return (
        (typeof p['method'] === 'string' && CLASSIFICATION_METHODS.has(p['method']))
        && typeof p['classes'] === 'number'
      );
    default:
      return false;
  }
}

export const styleCommands: Record<string, CommandEntry> = {
  apply_style_intent: {
    requiredParams: validIntent,
    async run(ctx): Promise<MapCommandResult> {
      const p = ctx.params as Record<string, unknown>;
      const layerId = p['layerId'] as string;
      const intent: StyleIntent = {
        kind: p['kind'] as StyleIntent['kind'],
        ...(typeof p['color'] === 'string' ? { color: p['color'] as string } : {}),
        ...(typeof p['palette'] === 'string' ? { palette: p['palette'] as string } : {}),
        ...(typeof p['opacity'] === 'number' ? { opacity: p['opacity'] } : {}),
        ...(typeof p['width'] === 'number' ? { width: p['width'] } : {}),
        ...(typeof p['size'] === 'number' ? { size: p['size'] } : {}),
        ...(typeof p['classes'] === 'number' ? { classes: p['classes'] } : {}),
        ...(typeof p['method'] === 'string' ? { method: p['method'] as StyleIntent['method'] } : {}),
        ...(typeof p['amount'] === 'number' ? { amount: p['amount'] } : {}),
      };
      const outcome = await applyStyleIntent(layerId, intent);
      if (outcome === 'invalid') {
        return { status: 'failed', error: 'invalid_style_intent' };
      }
      if (outcome === 'locked') {
        return { status: 'failed', error: 'layer_locked' };
      }
      return { status: 'succeeded', result: { layerId, kind: intent.kind } };
    },
  },
};
