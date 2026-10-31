import type { CommandEntry, MapCommandContext, MapCommandResult } from './types';

/**
 * Analysis workflow orchestration commands（goal-02 的 agent 编排工具自标识）。
 *
 * #535 幽灵命令不变量：后端 app/ 里发射的每个 `command` 字面量都必须
 * 存在于 COMMAND_CATALOGUE。analysis_workflow_tools 的四个工具在返回
 * dict 里带 `command: <工具名>` 自标识——沿 sceneCommands（#1829 清偿）
 * 的最小先例登记。
 *
 * 语义边界：这四个 command 是 **agent 编排通知**，不是前端地图动作——
 * 分析产物经 ref: 提货券 + MapSpec commit 通道（webgis_map_product 挂层）
 * 流动，不经 map-action 命令通道。若编排结果事件被路由到命令处理器
 * （历史通道兼容），在此显式 settle succeeded（观测性 ack——不假装执行
 * 了前端动作，也不留 unknown_command 一等失败路径）。
 */

function analysisWorkflowAck(ctx: MapCommandContext): MapCommandResult {
  void ctx;
  return {
    status: 'succeeded',
    note: 'agent analysis-workflow notification; results flow via ref + MapSpec commit channel',
  } as MapCommandResult;
}

const acceptAnyParams = (): boolean => true;

export const analysisWorkflowCommands: Record<string, CommandEntry> = {
  webgis_analysis_workflow_plan: {
    requiredParams: acceptAnyParams,
    run: analysisWorkflowAck,
  },
  webgis_analysis_workflow_run: {
    requiredParams: acceptAnyParams,
    run: analysisWorkflowAck,
  },
  webgis_analysis_workflow_status: {
    requiredParams: acceptAnyParams,
    run: analysisWorkflowAck,
  },
  webgis_analysis_workflow_recompute: {
    requiredParams: acceptAnyParams,
    run: analysisWorkflowAck,
  },
};
