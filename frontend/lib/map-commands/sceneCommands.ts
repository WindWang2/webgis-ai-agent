import type { CommandEntry, MapCommandContext, MapCommandResult } from './types';

/**
 * Scene orchestration commands（G3D04/#1829 的 agent 编排工具自标识）。
 *
 * #535 幽灵命令不变量：后端 app/ 里发射的每个 `command` 字面量都必须
 * 存在于 COMMAND_CATALOGUE。scene_orchestration_tools 的四个工具在返回
 * dict 里带 `command: <tool名>` 自标识（历史欠账：#1829 未登记前端目录，
 * catalogue-contract.test 在 master 上即红——本文件是最小非重叠清偿）。
 *
 * 语义边界：这四个 command 是 **agent 编排通知**，不是前端地图动作——
 * 场景/相机/分析的真实状态经 MapSpec commit 通道（set_scene/set_view
 * 突变 + work echo）流动，不经 map-action 命令通道。若编排结果事件被
 * 路由到命令处理器（历史通道兼容），在此显式 settle succeeded（观测性
 * ack——不假装执行了前端动作，也不留 unknown_command 一等失败路径）。
 */

function sceneOrchestrationAck(ctx: MapCommandContext): MapCommandResult {
  void ctx;
  return {
    status: 'succeeded',
    note: 'agent scene-orchestration notification; state flows via MapSpec commit channel',
  } as MapCommandResult;
}

const acceptAnyParams = (): boolean => true;

export const sceneCommands: Record<string, CommandEntry> = {
  get_3d_capabilities: {
    requiredParams: acceptAnyParams,
    run: sceneOrchestrationAck,
  },
  apply_scene_representation: {
    requiredParams: acceptAnyParams,
    run: sceneOrchestrationAck,
  },
  fly_camera: {
    requiredParams: acceptAnyParams,
    run: sceneOrchestrationAck,
  },
  run_3d_analysis: {
    requiredParams: acceptAnyParams,
    run: sceneOrchestrationAck,
  },
};
