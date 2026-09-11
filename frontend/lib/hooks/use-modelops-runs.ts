'use client';

/**
 * use-modelops-runs — 从本会话 chat 工具事件观察 ModelOps 推理运行。
 *
 * 后端无持久化运行历史端点（run_id 仅 cancel 可用，勘察报告 GAP #10）——
 * 这里只读 useChatStore 消息上的 toolCalls，绝不伪造历史。刷新页面后
 * 历史即消失，这是后端能力现状的诚实反映（PR 协调点）。
 */
import { useMemo } from 'react';
import { useChatStore } from '@/lib/store/useChatStore';

const RUN_TOOLS = new Set(['modelops_run_inference', 'modelops_run_promptable']);

export interface ModelOpsSessionRun {
  callId: string;
  tool: string;
  status: 'running' | 'completed' | 'failed';
  runId: string | null;
  modelId: string | null;
  taskType: string | null;
  reused: boolean | null;
  outputs: Record<string, { path?: string; data_object_id?: string; [key: string]: unknown }>;
  performance: Record<string, unknown> | null;
  error: string | null;
  startedAt: number | null;
  completedAt: number | null;
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === 'object' && value !== null ? (value as Record<string, unknown>) : null;
}

function parseModelId(rawArgs: unknown): string | null {
  if (typeof rawArgs !== 'string') return null;
  try {
    const parsed = asRecord(JSON.parse(rawArgs));
    const id = parsed?.model_id;
    return typeof id === 'string' ? id : null;
  } catch {
    return null;
  }
}

export function useModelopsRuns(): ModelOpsSessionRun[] {
  const messages = useChatStore((s) => s.messages);
  return useMemo(() => {
    const runs: ModelOpsSessionRun[] = [];
    for (const msg of messages) {
      const calls = Array.isArray(msg.toolCalls) ? msg.toolCalls : [];
      for (const raw of calls) {
        const call = asRecord(raw);
        if (!call || typeof call.tool !== 'string' || !RUN_TOOLS.has(call.tool)) continue;
        const result = asRecord(call.result);
        const outputsRaw = asRecord(result?.outputs);
        const outputs: ModelOpsSessionRun['outputs'] = {};
        if (outputsRaw) {
          for (const [role, val] of Object.entries(outputsRaw)) {
            const rec = asRecord(val);
            if (rec) outputs[role] = rec as ModelOpsSessionRun['outputs'][string];
          }
        }
        const err = call.error;
        runs.push({
          callId: typeof call.id === 'string' ? call.id : `${msg.id}-${runs.length}`,
          tool: call.tool,
          status: call.status === 'completed' || call.status === 'failed' ? call.status : 'running',
          runId: typeof result?.run_id === 'string' ? result.run_id : null,
          modelId: parseModelId(call.arguments),
          taskType: typeof result?.task_type === 'string' ? result.task_type : null,
          reused: typeof result?.reused === 'boolean' ? result.reused : null,
          outputs,
          performance: asRecord(result?.performance),
          error: typeof err === 'string' ? err : null,
          startedAt: typeof call.startedAt === 'number' ? call.startedAt : null,
          completedAt: typeof call.completedAt === 'number' ? call.completedAt : null,
        });
      }
    }
    return runs;
  }, [messages]);
}

export default useModelopsRuns;
