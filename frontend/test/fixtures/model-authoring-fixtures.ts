/**
 * model-authoring 端点 fixtures（测试注入；形状逐字对照
 * /api/v1/model-authoring 投影 —— store._row_to_dict / contracts
 * ModelFinding.to_bounded / bindings.py 回执）。
 */
import type {
  ModelCapabilities,
  ModelCatalogEntry,
  ModelDraft,
  ModelFinding,
  ModelInstantiateResult,
} from '@/lib/api/model-authoring';
import type { ModelDocument, ModelNode } from '@/lib/workflow/model-graph';
import type { WorkflowInstance, WorkflowNode } from '@/lib/api/workflow-runtime';

function node(partial: Partial<ModelNode> & { node_id: string }): ModelNode {
  return { kind: 'transform', title: partial.node_id, ...partial };
}

/** 三节点最小合法模型：subject 输入 → buffer → output。 */
export const sampleDocument: ModelDocument = {
  schema_version: '1',
  name: '径流适宜性',
  description: '缓冲分析演示模型',
  source: { kind: 'manual', ref: '' },
  params: [
    {
      name: 'buffer_m',
      kind: 'number',
      label: '缓冲半径',
      required: true,
      default: { value: 300 },
      minimum: 0,
      maximum: 5000,
      unit: 'm',
    },
  ],
  nodes: [
    node({ node_id: 'poi_in', kind: 'data_input', role: 'subject', title: '主体要素' }),
    node({ node_id: 'buffered', kind: 'transform', transform_op: 'buffer', title: '缓冲 300m', params: { values: { distance: { value: '$param:buffer_m' } } } }),
    node({ node_id: 'map_out', kind: 'cartography', title: '出图' }),
  ],
  edges: [
    { from_node: 'poi_in', from_port: 'subject', to_node: 'buffered', to_port: '' },
    { from_node: 'buffered', from_port: 'out', to_node: 'map_out', to_port: '' },
  ],
  output_node_ids: [],
  layout: {
    poi_in: { x: 24, y: 24 },
    buffered: { x: 224, y: 24 },
    map_out: { x: 424, y: 24 },
  },
  template_id: '',
};

export const sampleDraft: ModelDraft = {
  id: 'draft-1',
  project_id: 'proj-1',
  model_slug: 'runoff_suitability',
  name: '径流适宜性',
  description: '缓冲分析演示模型',
  document: sampleDocument,
  revision_no: 3,
  document_fingerprint: 'fp_aaaa1111',
  source_kind: 'manual',
  source_ref: '',
  template_id: '',
  latest_published_package_id: '',
  latest_published_version: '',
  created_by: 'u1',
  created_at: '2026-11-25T00:00:00Z',
  updated_at: '2026-11-25T12:00:00Z',
};

export const emptyDocument: ModelDocument = {
  schema_version: '1',
  name: '新模型',
  description: '',
  source: { kind: 'manual', ref: '' },
  nodes: [],
  edges: [],
  output_node_ids: [],
  layout: {},
};

export const emptyDraft: ModelDraft = {
  ...sampleDraft,
  id: 'draft-2',
  model_slug: 'fresh_model',
  name: '新模型',
  document: emptyDocument,
  revision_no: 1,
  document_fingerprint: 'fp_empty',
  latest_published_package_id: '',
  latest_published_version: '',
};

export const capabilitiesFixture: ModelCapabilities = {
  transform_ops: ['buffer', 'clip', 'dissolve', 'overlay', 'filter', 'reproject'],
  data_roles: [
    'subject', 'measure', 'boundary', 'denominator', 'network', 'elevation',
    'hazard', 'receptor', 'population', 'facility', 'criteria', 'constraint',
    'reference', 'baseline', 'target_time', 'comparison_time',
  ],
  capabilities: [
    { capability: 'cap.hotspot.analysis', input_artifact_types: ['feature_collection', 'stats_table'], output_artifact_types: ['hotspot_result'], status: 'native' },
    { capability: 'cap.density.estimate', input_artifact_types: ['point_feature_set'], output_artifact_types: ['density_surface'], status: 'native' },
    { capability: 'cap.proximity.service_area', input_artifact_types: ['feature_collection', 'network_graph'], output_artifact_types: ['service_area'], status: 'native' },
  ],
};

/** 单 finding 构造（detail_zh/en 缺省取 contracts.FINDING_MESSAGES 同款双语短述）。 */
export function finding(partial: Partial<ModelFinding> & { code: string }): ModelFinding {
  return {
    severity: 'error',
    node_id: '',
    edge: '',
    param: '',
    detail_zh: '模型存在问题',
    detail_en: 'Model has a problem',
    detail: 'detail',
    ...partial,
  };
}

/** 双语短述对照 backend contracts.FINDING_MESSAGES（前端只投影不发明）。 */
export const cycleFindings: ModelFinding[] = [
  finding({
    code: 'MODEL_CYCLE',
    severity: 'error',
    node_id: 'buffered',
    detail_zh: '存在环依赖',
    detail_en: 'Cycle detected',
    detail: 'cycle: buffered -> buffered',
  }),
  finding({
    code: 'INPUT_NODE_MISSING',
    severity: 'error',
    node_id: 'map_out',
    detail_zh: 'analysis/cartography 节点没有数据输入',
    detail_en: 'Node has no data input',
    detail: 'cartography node has no data input edge',
  }),
];

function workflowNode(partial: Partial<WorkflowNode> & { node_id: string }): WorkflowNode {
  return {
    state: 'PENDING',
    attempts: 0,
    error_code: null,
    bound_ref: null,
    output_ref: null,
    reused: false,
    reuse_evidence: {},
    binding_violations: [],
    ...partial,
  };
}

/** 实例投影（workflow-runtime 同形；transform 节点 wire id 带前缀）。 */
export const instanceFixture: WorkflowInstance = {
  instance_id: 'wi-model-1',
  package_id: 'pkg.model.runoff_suitability',
  package_version: '1.0.0',
  package_fingerprint: 'fp_aaaa1111',
  status: 'running',
  revision: 1,
  cancel_requested: false,
  nodes: [
    workflowNode({ node_id: 'poi_in', state: 'SUCCEEDED', attempts: 1, bound_ref: 'ref://sess/poi', output_ref: 'ref://out/poi' }),
    workflowNode({ node_id: 'transform:buffer:buffered', state: 'RUNNING', attempts: 1 }),
    workflowNode({ node_id: 'map_out', state: 'PENDING', attempts: 0 }),
  ],
  counts: { SUCCEEDED: 1, RUNNING: 1, PENDING: 1 },
  decisions: [],
  pending_changes: [],
  error_code: null,
  error_detail: null,
};

export const instantiateFixture: ModelInstantiateResult = {
  model_slug: 'runoff_suitability',
  package: { package_id: 'pkg.model.runoff_suitability', version: '1.0.0', fingerprint: 'fp_aaaa1111' },
  params_resolved: { buffer_m: 300 },
  node_params: {},
  bindings: {
    poi_in: { node: 'poi_in', ok: true, code: 'BOUND', bound_ref: 'ref://sess/poi' },
  },
  instance: instanceFixture,
};

export const catalogFixture: ModelCatalogEntry[] = [
  {
    model_slug: 'runoff_suitability',
    name: '径流适宜性',
    published: true,
    latest_published_version: '1.0.0',
    versions: [{ version: '1.0.0', status: 'published', fingerprint: 'fp_aaaa1111' }],
  },
];

/** 统一错误信封（409 CAS 冲突等）。 */
export function envelopeError(code: string, detail: string, findings: ModelFinding[] = []): Record<string, unknown> {
  return {
    code: 'CONFLICT',
    success: false,
    message: '资源状态冲突',
    data: { code, detail, ...(findings.length ? { findings } : {}) },
  };
}
