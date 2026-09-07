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
      const hud = ctx.getHudState();
      const outcome = await applyStyleIntent(layerId, intent);
      // Review R1（a11y MAJOR-6）：失败必须可感知 —— failed 只进 ack 不进
      // 任何 live region；这里补 system message（经 toast live region 播报）。
      const announce = (msg: string) => {
        try {
          hud.setPendingSystemMessage(`[系统通知] ${msg}`);
        } catch { /* 通知通道不可用不得阻断 */ }
      };
      if (outcome === 'invalid') {
        announce(`样式调整未生效：图层 ${layerId} 不存在或不支持该样式意图（需按合约重新构造意图）`);
        return { status: 'failed', error: 'invalid_style_intent' };
      }
      if (outcome === 'locked') {
        announce(`样式调整未生效：图层 ${layerId} 已被用户锁定`);
        return { status: 'failed', error: 'layer_locked' };
      }
      if (outcome === 'thematic_protected') {
        // Review R1（GIS F3）：分级/连续专题层的色彩编码受保护 —— 色彩意图
        // 必须走 reclassify 通道（后端），flat color 会抹平分级编码。
        announce(`样式调整未生效：图层 ${layerId} 是分级/连续专题着色，直接改色会破坏分级编码 —— 请走重分类通道`);
        return { status: 'failed', error: 'thematic_color_protected' };
      }
      return { status: 'succeeded', result: { layerId, kind: intent.kind } };
    },
  },
};
