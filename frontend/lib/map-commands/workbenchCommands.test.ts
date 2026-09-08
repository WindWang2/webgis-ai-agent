/**
 * Workbench V4（Wave 3）：set_mode shell 命令合约测试。
 * agent → 确定性 UI 动作（MapAction 队列同域）：词表校验、幂等、
 * modeOrigin='agent' 回执、系统通知。
 */
import { describe, it, expect, vi } from 'vitest';
import { workbenchCommands } from './workbenchCommands';

function makeCtx(mode: string, params: Record<string, unknown>) {
  const hud = {
    mode,
    setWorkbenchMode: vi.fn(),
    setPendingSystemMessage: vi.fn(),
  };
  return {
    map: {},
    popAction: vi.fn(),
    setDeferredPop: vi.fn(),
    safePop: vi.fn(),
    getHudState: () => hud,
    setSelectedBaseLayer: vi.fn(),
    command: 'set_mode',
    params: params as never,
    hud,
  };
}

describe('set_mode shell command', () => {
  it('requiredParams 只接受封闭模式词表', () => {
    const entry = workbenchCommands.set_mode;
    expect(entry.requiredParams({ mode: 'explore' })).toBe(true);
    expect(entry.requiredParams({ mode: 'analyze' })).toBe(true);
    expect(entry.requiredParams({ mode: 'compose' })).toBe(true);
    expect(entry.requiredParams({ mode: 'party' })).toBe(false);
    expect(entry.requiredParams({})).toBe(false);
  });

  it('切换到新模式：modeOrigin=agent + 系统通知，返回 succeeded', () => {
    const ctx = makeCtx('explore', { mode: 'compose' });
    const result = workbenchCommands.set_mode.run(ctx);
    expect(result).toEqual({ status: 'succeeded' });
    expect(ctx.hud.setWorkbenchMode).toHaveBeenCalledWith('compose', 'agent');
    expect(ctx.hud.setPendingSystemMessage).toHaveBeenCalled();
  });

  it('同模式重复声明幂等：不重复切换、不发通知', () => {
    const ctx = makeCtx('compose', { mode: 'compose' });
    const result = workbenchCommands.set_mode.run(ctx);
    expect(result).toEqual({ status: 'succeeded' });
    expect(ctx.hud.setWorkbenchMode).not.toHaveBeenCalled();
    expect(ctx.hud.setPendingSystemMessage).not.toHaveBeenCalled();
  });
});
