/**
 * ModelOps API 测试 — executeToolDirect 工具名/参数/响应透传。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { executeToolDirect } from '@/lib/api/chat';
import { fetchModelopsHistory, inspectModelopsModel, listModelopsModels } from './modelops';

vi.mock('@/lib/api/chat', () => ({
  executeToolDirect: vi.fn(),
}));

const mockExecute = vi.mocked(executeToolDirect);

beforeEach(() => {
  vi.clearAllMocks();
});

describe('listModelopsModels', () => {
  it('以空参数调用 modelops_list_models 并透传列表', async () => {
    mockExecute.mockResolvedValueOnce({
      models: [
        {
          model_id: 'seg.unet',
          model_version: '1.0.0',
          task_types: ['image_segmentation'],
          provider_type: 'onnx',
          checksum: 'abc123…',
          owner_scope: 'org:1',
          license: 'Apache-2.0',
        },
      ],
      count: 1,
    });
    const res = await listModelopsModels();
    expect(mockExecute).toHaveBeenCalledWith('modelops_list_models', {});
    expect(res.count).toBe(1);
    expect(res.models[0]?.model_id).toBe('seg.unet');
  });
});

describe('inspectModelopsModel', () => {
  it('携带 model_id 调用 modelops_inspect_model', async () => {
    mockExecute.mockResolvedValueOnce({
      descriptor: { model_id: 'seg.unet', model_version: '1.0.0', provenance: {} },
      provider_capabilities: { onnx: true },
      owner_scope: 'org:1',
      revision: 3,
      package_report: null,
      lineage: { deployment_state: 'active', latest_metrics: null, event_count: 0 },
    });
    const res = await inspectModelopsModel('seg.unet');
    expect(mockExecute).toHaveBeenCalledWith('modelops_inspect_model', { model_id: 'seg.unet' });
    expect(res.revision).toBe(3);
    expect(res.lineage.deployment_state).toBe('active');
  });
});

describe('fetchModelopsHistory', () => {
  it('调用 modelops_model_history 并透传事件表', async () => {
    mockExecute.mockResolvedValueOnce({
      model_id: 'seg.unet',
      versions: {
        '1.0.0': {
          events: [
            { seq: 1, ts: 1757000000, model_id: 'seg.unet', model_version: '1.0.0', event_type: 'training_metrics', actor: 'op', payload: {} },
          ],
          deployment_state: 'active',
        },
      },
      event_count: 1,
    });
    const res = await fetchModelopsHistory('seg.unet');
    expect(mockExecute).toHaveBeenCalledWith('modelops_model_history', { model_id: 'seg.unet' });
    expect(res.versions['1.0.0']?.events).toHaveLength(1);
  });
});
