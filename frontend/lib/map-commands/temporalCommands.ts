import type { CommandEntry, MapCommandContext, MapCommandResult } from './types';

/**
 * Temporal view commands（GOAL 03 M3/M4 —— agent 时序控制工具自标识）。
 *
 * #535 幽灵命令不变量：temporal_view_* 工具族的真实状态经 MapSpec commit
 * 通道（SetTemporalViewIntent → work echo → commitMapSpecDocument）流动；
 * 若工具回执的 command 自标识被路由到命令处理器（历史通道兼容），在此
 * 显式 settle succeeded（观测性 ack —— 与 sceneCommands 同款语义边界）。
 */

function temporalAck(ctx: MapCommandContext): MapCommandResult {
  void ctx;
  return {
    status: 'succeeded',
    note: 'agent temporal-view notification; state flows via MapSpec temporal commit channel',
  } as MapCommandResult;
}

const acceptAnyParams = (): boolean => true;

export const temporalCommands: Record<string, CommandEntry> = {
  temporal_view_bind: { requiredParams: acceptAnyParams, run: temporalAck },
  temporal_view_control: { requiredParams: acceptAnyParams, run: temporalAck },
  temporal_view_compare: { requiredParams: acceptAnyParams, run: temporalAck },
  temporal_view_unbind: { requiredParams: acceptAnyParams, run: temporalAck },
  temporal_view_status: { requiredParams: acceptAnyParams, run: temporalAck },
};
