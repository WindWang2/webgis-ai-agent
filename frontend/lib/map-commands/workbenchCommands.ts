import type { CommandEntry, MapCommandResult } from './types';
import { WORKBENCH_MODES, type WorkbenchMode } from '@/lib/store/slices/workbenchSlice';

/**
 * Workbench shell commands（Workbench V4 / Wave 3）—— agent 的确定性 UI
 * 动作合约（shell 域此前缺失：catalogue 全是地图语义命令）。
 *
 * set_mode：切换 Explore/Analyze/Compose 工作台模式。走与地图命令同一
 * MapAction 队列（可观测、可回执、ops log 可查）；modeOrigin='agent' 让
 * UI 展示可发现回执（用户一键返回，不丢自己的上下文 —— 模式切换保留
 * 每个模式自己的 tab 记忆与面板状态，不复制地图状态）。
 */

export function isValidWorkbenchMode(value: unknown): value is WorkbenchMode {
  return typeof value === 'string' && (WORKBENCH_MODES as readonly string[]).includes(value);
}

export const workbenchCommands: Record<string, CommandEntry> = {
  set_mode: {
    requiredParams: (p) => isValidWorkbenchMode((p as { mode?: unknown }).mode),
    run(ctx): MapCommandResult {
      const mode = (ctx.params as { mode?: unknown }).mode;
      if (!isValidWorkbenchMode(mode)) {
        return { status: 'failed', error: 'invalid_mode' };
      }
      const hud = ctx.getHudState();
      // 幂等：目标模式 == 当前模式 → succeeded（agent 重复声明不是错误）。
      if (hud.mode !== mode) {
        hud.setWorkbenchMode(mode, 'agent');
        try {
          hud.setPendingSystemMessage(
            `[系统通知] 已切换到${
              mode === 'explore' ? '探索' : mode === 'analyze' ? '分析' : '制图'
            }工作台模式。用户可随时从左侧导航栏一键切回。`,
          );
        } catch {
          /* 通知通道不可用不得阻断命令 */
        }
      }
      return { status: 'succeeded' };
    },
  },
};
