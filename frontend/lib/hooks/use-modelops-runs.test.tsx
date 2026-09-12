/**
 * use-modelops-runs 测试 — 从 chat store 消息提取本会话推理运行。
 * 诚实性约束：不伪造历史；缺失/畸形字段安全降级为 null。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useChatStore, type ChatMessage } from '@/lib/store/useChatStore';
import { useModelopsRuns } from './use-modelops-runs';

function msg(overrides: Partial<ChatMessage>): ChatMessage {
  return {
    id: `m${Math.random()}`,
    role: 'assistant',
    content: '',
    timestamp: null,
    ...overrides,
  };
}

beforeEach(() => {
  useChatStore.setState({ messages: [], streamingToken: '' });
});

describe('useModelopsRuns', () => {
  it('从 toolCalls 中提取 run_id/model/任务/输出，非 modelops 工具忽略', () => {
    useChatStore.setState({
      messages: [
        msg({
          toolCalls: [
            {
              id: 'c1',
              tool: 'modelops_run_inference',
              status: 'completed',
              arguments: JSON.stringify({ model_id: 'seg.unet', source_uri: 's3://a.tif' }),
              result: {
                run_id: 'run_abc',
                status: 'completed',
                reused: false,
                task_type: 'image_segmentation',
                outputs: {
                  mask: { path: '/data/out.tif', data_object_id: 'dob_1' },
                },
                performance: { seconds: 3.2 },
              },
              startedAt: 1757000000000,
              completedAt: 1757000003200,
            },
            {
              id: 'c2',
              tool: 'webgis_buffer',
              status: 'completed',
              result: { geojson: {} },
            },
          ],
        }),
      ],
    });
    const { result } = renderHook(() => useModelopsRuns());
    expect(result.current).toHaveLength(1);
    const run = result.current[0]!;
    expect(run.runId).toBe('run_abc');
    expect(run.modelId).toBe('seg.unet');
    expect(run.taskType).toBe('image_segmentation');
    expect(run.reused).toBe(false);
    expect(run.outputs.mask?.data_object_id).toBe('dob_1');
    expect(run.status).toBe('completed');
  });

  it('运行中/失败与畸形结果安全降级（runId=null 不抛错）', () => {
    useChatStore.setState({
      messages: [
        msg({
          toolCalls: [
            { id: 'c3', tool: 'modelops_run_promptable', status: 'running', arguments: 'not-json' },
            { id: 'c4', tool: 'modelops_run_inference', status: 'failed', error: 'CUDA OOM' },
          ],
        }),
      ],
    });
    const { result } = renderHook(() => useModelopsRuns());
    expect(result.current).toHaveLength(2);
    expect(result.current[0]?.runId).toBeNull();
    expect(result.current[0]?.status).toBe('running');
    expect(result.current[1]?.status).toBe('failed');
    expect(result.current[1]?.error).toBe('CUDA OOM');
  });

  it('无消息时为空数组（诚实空态）', () => {
    const { result } = renderHook(() => useModelopsRuns());
    expect(result.current).toHaveLength(0);
  });
});
