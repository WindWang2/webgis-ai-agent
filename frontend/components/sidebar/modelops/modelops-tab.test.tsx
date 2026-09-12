/**
 * ModelOpsTab 测试 — 注册表（经 executeToolDirect）+ 本会话运行 + 诚实呈现。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ModelOpsTab } from './modelops-tab';
import { executeToolDirect } from '@/lib/api/chat';
import { useChatStore, type ChatMessage } from '@/lib/store/useChatStore';

vi.mock('@/lib/api/chat', () => ({
  executeToolDirect: vi.fn(),
}));

const mockExecute = vi.mocked(executeToolDirect);

const listResult = {
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
};

const inspectResult = {
  descriptor: {
    model_id: 'seg.unet',
    model_version: '1.0.0',
    provider_type: 'onnx',
    license: 'Apache-2.0',
    task_types: ['image_segmentation'],
    input_modalities: ['raster'],
    input_bands: 3,
    output_types: ['mask'],
    random_seed_policy: 'deterministic',
    spatial: {
      resolution_range: { min_m_per_px: 0.1, max_m_per_px: 2.0 },
      allow_reproject: false,
      chip_size: 512,
    },
    device_requirements: { required: 'cuda', allow_cpu_fallback: true, min_vram_mb: 4096 },
    provenance: { source: 'seed' },
  },
  provider_capabilities: { onnxruntime: '1.16' },
  owner_scope: 'org:1',
  revision: 3,
  package_report: null,
  lineage: { deployment_state: 'active', latest_metrics: null, event_count: 1 },
};

const historyResult = {
  model_id: 'seg.unet',
  versions: {
    '1.0.0': {
      events: [
        {
          seq: 2,
          ts: 1757000000,
          model_id: 'seg.unet',
          model_version: '1.0.0',
          event_type: 'promotion',
          actor: 'op',
          payload: {},
        },
      ],
      deployment_state: 'active',
    },
  },
  event_count: 1,
};

function msg(overrides: Partial<ChatMessage>): ChatMessage {
  return { id: `m${Math.random()}`, role: 'assistant', content: '', timestamp: null, ...overrides };
}

beforeEach(() => {
  vi.clearAllMocks();
  useChatStore.setState({ messages: [], streamingToken: '' });
  mockExecute.mockImplementation((tool: string) => {
    if (tool === 'modelops_list_models') return Promise.resolve(listResult);
    if (tool === 'modelops_inspect_model') return Promise.resolve(inspectResult);
    if (tool === 'modelops_model_history') return Promise.resolve(historyResult);
    return Promise.reject(new Error(`unknown tool ${tool}`));
  });
});

describe('ModelOpsTab', () => {
  it('注册表空是诚实空态（说明模型经工具链注册）', async () => {
    mockExecute.mockImplementation((tool: string) =>
      tool === 'modelops_list_models'
        ? Promise.resolve({ models: [], count: 0 })
        : Promise.resolve({}),
    );
    render(<ModelOpsTab />);
    expect(await screen.findByText('注册表中暂无模型')).toBeInTheDocument();
    expect(screen.getByText(/#1212 现状/)).toBeInTheDocument();
  });

  it('列表渲染模型卡片；#1212 固定说明在场', async () => {
    render(<ModelOpsTab />);
    expect(await screen.findByText('seg.unet')).toBeInTheDocument();
    expect(screen.getByText(/#1212 现状/)).toBeInTheDocument();
    expect(screen.getByText('image_segmentation')).toBeInTheDocument();
  });

  it('详情展示地理配准要求 / 确定性语义 / provenance 原文（不美化）', async () => {
    const user = userEvent.setup();
    render(<ModelOpsTab />);
    await user.click(await screen.findByRole('button', { name: /seg\.unet/ }));
    expect(await screen.findByText('地理配准要求')).toBeInTheDocument();
    expect(screen.getByText(/0\.1 – 2 m\/px/)).toBeInTheDocument();
    expect(screen.getByText(/random_seed_policy：deterministic/)).toBeInTheDocument();
    expect(screen.getByText(/"source": "seed"/)).toBeInTheDocument();
    expect(screen.getByText(/模型经工具关键词发现（#1212）/)).toBeInTheDocument();
    expect(screen.getByText(/版本事件（1）/)).toBeInTheDocument();
  });

  it('运行历史仅来自本会话观察，空态明示协调点', async () => {
    render(<ModelOpsTab />);
    expect(
      await screen.findByText(/本会话暂无推理调用/),
    ).toBeInTheDocument();
    expect(screen.getByText(/后端未提供持久化运行历史查询端点/)).toBeInTheDocument();
  });

  it('会话中的 modelops 工具调用出现在运行历史并展开产物', async () => {
    const user = userEvent.setup();
    useChatStore.setState({
      messages: [
        msg({
          toolCalls: [
            {
              id: 'c1',
              tool: 'modelops_run_inference',
              status: 'completed',
              arguments: JSON.stringify({ model_id: 'seg.unet' }),
              result: {
                run_id: 'run_xyz',
                status: 'completed',
                reused: true,
                task_type: 'image_segmentation',
                outputs: { mask: { path: '/data/mask.tif', data_object_id: 'dob_9' } },
                performance: { seconds: 2.5 },
              },
            },
          ],
        }),
      ],
    });
    render(<ModelOpsTab />);
    await user.click(await screen.findByRole('button', { name: /run_xyz/ }));
    expect(screen.getByText(/dob_9/)).toBeInTheDocument();
    expect(screen.getByText(/复用产物/)).toBeInTheDocument();
  });

  it('工具执行失败渲染 role=alert（不掩盖后端错误）', async () => {
    mockExecute.mockRejectedValue(new Error('tool dispatch failed'));
    render(<ModelOpsTab />);
    expect(await screen.findByRole('alert')).toHaveTextContent('tool dispatch failed');
  });
});
