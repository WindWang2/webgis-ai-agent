/**
 * ModelBuilderTab 面板集成测试（WP3 验收面）。
 *
 * fetch 注入 fixtures：palette 渲染 / 添加节点 → 画布出现 / 连线 → 保存
 * CAS PUT 断言 / validate findings 渲染与节点联动 / 运行 overlay 状态色 /
 * 409 REVISION_CONFLICT 冲突提示。文案按 defaultTranslator 解析
 * （照 runtime-inspector.test.tsx 模式 —— 不写死中文）。
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { defaultTranslator } from '@/lib/i18n/translator';
import { ModelBuilderTab } from './model-builder-tab';
import {
  capabilitiesFixture,
  cycleFindings,
  emptyDraft,
  instanceFixture,
  instantiateFixture,
  sampleDraft,
} from '@/test/fixtures/model-authoring-fixtures';
import type { ModelDraft } from '@/lib/api/model-authoring';

/* ── hud store mock：activeProjectId 是面板的项目来源 ── */
const store: Record<string, unknown> = {
  activeProjectId: 'proj-1',
  pushOpLog: vi.fn(),
};
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: Object.assign(
    (selector: (s: unknown) => unknown) => selector(store),
    { getState: () => store },
  ),
}));

/** fetch stub：按 (method, path 前缀) 路由，可记录调用。 */
let calls: Array<{ method: string; path: string; body: unknown }> = [];
let routes: Array<{ method: string; path: string; status: number; body: unknown }> = [];

function stubFetch() {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = (init?.method ?? 'GET').toUpperCase();
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, path: url.pathname, body });
    const route =
      routes.find((r) => r.method === method && r.path === url.pathname) ??
      routes.find((r) => r.method === method && url.pathname.startsWith(r.path));
    if (!route) throw new Error(`no route: ${method} ${url.pathname}`);
    return new Response(JSON.stringify(route.body), {
      status: route.status,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

function baseRoutes(draft: ModelDraft = sampleDraft) {
  return [
    { method: 'GET', path: '/api/v1/model-authoring/capabilities', status: 200, body: { success: true, ...capabilitiesFixture } },
    { method: 'GET', path: '/api/v1/model-authoring/projects/proj-1/models', status: 200, body: { success: true, models: [draft] } },
    { method: 'GET', path: '/api/v1/model-authoring/projects/proj-1/models/runoff_suitability', status: 200, body: { success: true, draft } },
    { method: 'PUT', path: '/api/v1/model-authoring/projects/proj-1/models/runoff_suitability', status: 200, body: { success: true, draft: { ...draft, revision_no: draft.revision_no + 1 } } },
    { method: 'POST', path: '/api/v1/model-authoring/projects/proj-1/models/runoff_suitability/validate', status: 200, body: { success: true, valid: false, findings: cycleFindings, document_fingerprint: 'fp_v' } },
    { method: 'POST', path: '/api/v1/model-authoring/projects/proj-1/models/runoff_suitability/instantiate', status: 200, body: { success: true, ...instantiateFixture } },
    { method: 'POST', path: '/api/v1/model-authoring/instances/wi-model-1/run', status: 200, body: { success: true, run: { status: 'running' }, node_params: {} } },
    { method: 'GET', path: '/api/v1/workflow-runtime/instances/wi-model-1', status: 200, body: { success: true, instance: instanceFixture } },
  ];
}

// 带 ICU 占位符的键必须传参 —— 缺参 next-intl 走 getMessageFallback（返回键路径）。
const T = (key: string, params?: Record<string, string | number>) =>
  defaultTranslator(`modelBuilder.${key}`, params);

async function renderTab() {
  render(<ModelBuilderTab />);
  // 等草稿加载完成（画布节点出现）。
  await waitFor(() => expect(screen.getByTestId('canvas-node-poi_in')).toBeInTheDocument());
}

beforeEach(() => {
  calls = [];
  routes = baseRoutes();
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('ModelBuilderTab（WP3 验收）', () => {
  it('palette 从 capabilities 渲染分区与条目（aria 可达）', async () => {
    await renderTab();
    expect(screen.getByText(T('palette.sectionTransform'))).toBeInTheDocument();
    const addItem = screen.getByRole('button', { name: T('aria.paletteItem', { name: 'clip' }) });
    expect(addItem).toHaveAttribute('aria-label');
  });

  it('添加节点 → 画布出现新节点（自动找空位）', async () => {
    await renderTab();
    expect(screen.queryByTestId('canvas-node-tf_clip')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: T('aria.paletteItem', { name: 'clip' }) }));
    expect(screen.getByTestId('canvas-node-tf_clip')).toBeInTheDocument();
    // dirty 提示出现，保存按钮可用。
    expect(screen.getByText(T('toolbar.dirtyHint'))).toBeInTheDocument();
  });

  it('连线 + 保存：PUT body 携带 document 与 expected_revision（CAS）', async () => {
    await renderTab();
    // 连线：poi_in.subject → map_out.in（fixture 之外的新边；宽→宽兼容）。
    const outPort = screen.getByTestId('canvas-node-poi_in').querySelector('[data-port="out"]') as Element;
    const inPort = screen.getByTestId('canvas-node-map_out').querySelector('[data-port="in"]') as Element;
    fireEvent.pointerDown(outPort, { pointerId: 1, button: 0 });
    fireEvent.pointerUp(inPort, { pointerId: 1 });
    await waitFor(() => expect(screen.getByTestId('save-btn')).toBeEnabled());
    fireEvent.click(screen.getByTestId('save-btn'));
    await waitFor(() => expect(screen.getByText(T('toolbar.saved'))).toBeInTheDocument());
    const put = calls.find((c) => c.method === 'PUT' && c.path.endsWith('/models/runoff_suitability')) as
      | { method: string; path: string; body: { expected_revision: number; document: { schema_version: string; edges: unknown[] } } }
      | undefined;
    expect(put).toBeDefined();
    expect(put?.body.expected_revision).toBe(sampleDraft.revision_no);
    expect(put?.body.document.schema_version).toBe('1');
    // 新边在 PUT 的 document 里（from_port = role 语义；to_port 主输入归一化空串）。
    expect(put?.body.document.edges).toEqual([
      ...sampleDraft.document.edges,
      { from_node: 'poi_in', from_port: 'subject', to_node: 'map_out', to_port: '' },
    ]);
  });

  it('validate findings 渲染；node_id 点击联动画布选中（属性面板出现节点）', async () => {
    await renderTab();
    fireEvent.click(screen.getByTestId('validate-btn'));
    await waitFor(() => expect(screen.getByText('MODEL_CYCLE')).toBeInTheDocument());
    // findings 的定位按钮 → 属性面板渲染该节点字段（nodeId input 值 = buffered）。
    fireEvent.click(screen.getAllByRole('button', { name: T('findings.jumpToNode', { node: 'buffered' }) })[0]);
    await waitFor(() => expect(screen.getByTestId('node-props-panel')).toBeInTheDocument());
    expect(screen.getByLabelText(T('props.nodeId'))).toHaveValue('buffered');
    // 选中节点是 transform → 属性面板出现算子下拉（词表来自目录）。
    expect(screen.getByLabelText(T('props.transformOp'))).toBeInTheDocument();
  });

  it('运行 overlay：instantiate → run → 轮询实例投影 → 节点态描边色', async () => {
    await renderTab();
    // 运行要求已保存（非 dirty）—— 加一个节点再保存（save 按钮仅在 dirty 时可用）。
    fireEvent.click(screen.getByRole('button', { name: T('aria.paletteItem', { name: 'clip' }) }));
    await waitFor(() => expect(screen.getByTestId('save-btn')).toBeEnabled());
    fireEvent.click(screen.getByTestId('save-btn'));
    await waitFor(() => expect(screen.getByText(T('toolbar.saved'))).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('run-btn'));
    // instantiate + run 两个 POST 都发出。
    await waitFor(() =>
      expect(calls.some((c) => c.path.endsWith('/instantiate'))).toBe(true),
    );
    await waitFor(() =>
      expect(calls.some((c) => c.path === '/api/v1/model-authoring/instances/wi-model-1/run')).toBe(true),
    );
    // 轮询命中 workflow-runtime 实例投影；transform wire id 还原为作者 id：
    // buffered(RUNNING) → info 描边，poi_in(SUCCEEDED) → success 描边。
    await waitFor(() => expect(screen.getByTestId('run-status')).toBeInTheDocument());
    await waitFor(() => {
      expect(
        screen.getByTestId('canvas-node-buffered').querySelector('rect')?.classList.contains('stroke-status-info'),
      ).toBe(true);
      expect(
        screen.getByTestId('canvas-node-poi_in').querySelector('rect')?.classList.contains('stroke-status-success'),
      ).toBe(true);
    });
    // 绑定回执逐节点展示（poi_in BOUND）。
    expect(screen.getByTestId('canvas-node-poi_in').getAttribute('aria-label')).toContain('ref://sess/poi');
  });

  it('409 REVISION_CONFLICT → 冲突提示 + 刷新按钮', async () => {
    routes = baseRoutes().map((r) =>
      r.method === 'PUT'
        ? {
            ...r,
            status: 409,
            body: { code: 'CONFLICT', success: false, message: '资源状态冲突', data: { code: 'REVISION_CONFLICT', detail: 'rev 3 stale' } },
          }
        : r,
    );
    await renderTab();
    fireEvent.click(screen.getByRole('button', { name: T('aria.paletteItem', { name: 'clip' }) }));
    await waitFor(() => expect(screen.getByTestId('save-btn')).toBeEnabled());
    fireEvent.click(screen.getByTestId('save-btn'));
    await waitFor(() => expect(screen.getByTestId('builder-notice')).toBeInTheDocument());
    expect(screen.getByTestId('builder-notice')).toHaveTextContent(T('errors.conflict'));
    expect(screen.getByTestId('refresh-draft-btn')).toBeInTheDocument();
  });

  it('撤销/恢复：图编辑命令入栈（add 节点 → undo 消失 → redo 回来）', async () => {
    await renderTab();
    fireEvent.click(screen.getByRole('button', { name: T('aria.paletteItem', { name: 'clip' }) }));
    expect(screen.getByTestId('canvas-node-tf_clip')).toBeInTheDocument();
    // undo（WorkbenchCommand 快照对；命令栈在 lib/workbench/undo.ts）。
    fireEvent.click(screen.getByRole('button', { name: T('toolbar.undo') }));
    expect(screen.queryByTestId('canvas-node-tf_clip')).toBeNull();
    // redo。
    fireEvent.click(screen.getByRole('button', { name: T('toolbar.redo') }));
    expect(screen.getByTestId('canvas-node-tf_clip')).toBeInTheDocument();
  });

  it('无项目时引导先选项目', () => {
    store.activeProjectId = null;
    render(<ModelBuilderTab />);
    expect(screen.getByText(T('empty.noProjectTitle'))).toBeInTheDocument();
    store.activeProjectId = 'proj-1';
  });

  it('空项目：新建草稿入口（create → POST /models）', async () => {
    routes = [
      { method: 'GET', path: '/api/v1/model-authoring/capabilities', status: 200, body: { success: true, ...capabilitiesFixture } },
      { method: 'GET', path: '/api/v1/model-authoring/projects/proj-1/models', status: 200, body: { success: true, models: [] } },
      { method: 'POST', path: '/api/v1/model-authoring/projects/proj-1/models', status: 200, body: { success: true, draft: emptyDraft } },
    ];
    render(<ModelBuilderTab />);
    await waitFor(() => expect(screen.getByText(T('empty.noDraftsTitle'))).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('new-draft-btn'));
    fireEvent.change(screen.getByLabelText(T('draft.slugLabel')), { target: { value: 'fresh_model' } });
    fireEvent.click(screen.getByTestId('confirm-create-btn'));
    await waitFor(() =>
      expect(calls.some((c) => c.method === 'POST' && c.path.endsWith('/models'))).toBe(true),
    );
    const post = calls.find((c) => c.method === 'POST' && c.path.endsWith('/models')) as
      | { method: string; path: string; body: { model_slug: string; document: { nodes: unknown[] } } }
      | undefined;
    expect(post?.body.model_slug).toBe('fresh_model');
    expect(post?.body.document.nodes).toEqual([]);
  });
});
