/**
 * ModelOps API — 经 POST /api/v1/chat/tools/execute（executeToolDirect）驱动
 * modelops agent 工具（契约见 frontend/docs/knowledge-market-recon.md §3）。
 *
 * 后端事实（UI 不得美化）：
 * - ModelOps **没有专用 HTTP 路由**；注册表 / inspect / history 全部是 agent
 *   工具。本模块把工具返回的 dict 原样映射为 TS 类型（字段名与后端一致）。
 * - 无持久化运行历史端点（run_id 仅 cancel 可用）→ 运行历史由
 *   use-modelops-runs 从本会话 chat 工具事件观察，不伪造。
 * - #1212：没有字段区分「工具关键词发现 vs 能力图投影」；descriptor.provenance
 *   是自由 dict，registered_by 不在列表投影里 —— UI 如实展示原文，不美化。
 */
import { executeToolDirect } from './chat';

/**
 * 产生推理 run 的工具名（tool-call-card 跳转链接与 use-modelops-runs
 * 共用同一词表，防漂移）。
 */
export const MODELOPS_RUN_TOOLS: readonly string[] = [
  'modelops_run_inference',
  'modelops_run_promptable',
];

/** modelops_list_models 的列表投影（service.py:304-325，checksum 为截断串）。 */
export interface ModelOpsListItem {
  model_id: string;
  model_version: string;
  task_types: string[];
  provider_type: string;
  checksum: string;
  owner_scope: string;
  license: string;
}

export interface ModelOpsListResult {
  models: ModelOpsListItem[];
  count: number;
}

/** GeoModelDescriptor.as_dict() 中 UI 消费的形状（descriptor.py:236-293）。 */
export interface ModelOpsDescriptor {
  model_id?: string;
  model_version?: string;
  checksum?: string;
  provider_type?: string;
  provider_ref?: string;
  provider_semantic_version?: string;
  task_types?: string[];
  input_modalities?: string[];
  input_bands?: number;
  band_order?: string[];
  output_types?: string[];
  license?: string;
  artifact_format?: string;
  random_seed_policy?: 'deterministic' | 'fixed_seed' | 'caller_seeded' | 'unseeded';
  spatial?: {
    chip_size?: number;
    context_size?: number;
    stride?: number;
    overlap_px?: number;
    crs_requirements?: unknown;
    resolution_range?: { min_m_per_px?: number; max_m_per_px?: number };
    resampling_policy?: string;
    allow_reproject?: boolean;
    min_valid_data_ratio?: number;
    [key: string]: unknown;
  };
  temporal?: Record<string, unknown>;
  device_requirements?: {
    required?: 'cpu' | 'cuda';
    allow_cpu_fallback?: boolean;
    min_vram_mb?: number;
    [key: string]: unknown;
  };
  class_schema?: { classes?: unknown[]; ignore_index?: number; nodata_class?: number };
  provenance?: Record<string, unknown>;
  [key: string]: unknown;
}

export interface ModelOpsInspectResult {
  descriptor: ModelOpsDescriptor;
  provider_capabilities: Record<string, unknown>;
  owner_scope: string;
  revision: number;
  package_report: Record<string, unknown> | null;
  lineage: {
    deployment_state: string;
    latest_metrics: Record<string, unknown> | null;
    event_count: number;
  };
}

export interface ModelOpsLineageEvent {
  seq: number;
  ts: number;
  model_id: string;
  model_version: string;
  event_type: string;
  actor: string;
  payload: Record<string, unknown>;
}

export interface ModelOpsHistoryResult {
  model_id: string;
  versions: Record<string, { events: ModelOpsLineageEvent[]; deployment_state: string }>;
  event_count: number;
}

export async function listModelopsModels(): Promise<ModelOpsListResult> {
  return (await executeToolDirect('modelops_list_models', {})) as unknown as ModelOpsListResult;
}

export async function inspectModelopsModel(modelId: string): Promise<ModelOpsInspectResult> {
  return (await executeToolDirect('modelops_inspect_model', {
    model_id: modelId,
  })) as unknown as ModelOpsInspectResult;
}

export async function fetchModelopsHistory(modelId: string): Promise<ModelOpsHistoryResult> {
  return (await executeToolDirect('modelops_model_history', {
    model_id: modelId,
  })) as unknown as ModelOpsHistoryResult;
}
