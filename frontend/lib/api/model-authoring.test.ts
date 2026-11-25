import { describe, expect, it, vi, afterEach } from 'vitest';
import {
  createModel,
  getModel,
  getCapabilities,
  instantiateModel,
  publishModel,
  runModelInstance,
  updateModel,
  validateModel,
  ModelAuthoringApiError,
} from './model-authoring';
import type { ModelDocument } from '@/lib/workflow/model-graph';

const DOC: ModelDocument = {
  schema_version: '1',
  name: 't',
  source: { kind: 'manual' },
  nodes: [],
  edges: [],
};

/** fetch stub：按 (method, path) 返回 {status, body}。 */
function stubFetch(routes: Array<{ method: string; path: string; status: number; body: unknown }>) {
  const fn = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    const method = (init?.method ?? 'GET').toUpperCase();
    const route = routes.find(
      (r) => r.method === method && (r.path === url.pathname || url.pathname.startsWith(r.path)),
    );
    if (!route) throw new Error(`no route: ${method} ${url.pathname}`);
    return new Response(JSON.stringify(route.body), {
      status: route.status,
      headers: { 'Content-Type': 'application/json' },
    });
  });
  vi.stubGlobal('fetch', fn);
  return fn;
}

describe('model-authoring typed client', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('GET /capabilities 解包词表', async () => {
    const fn = stubFetch([
      {
        method: 'GET',
        path: '/api/v1/model-authoring/capabilities',
        status: 200,
        body: {
          success: true,
          transform_ops: ['buffer'],
          data_roles: ['subject'],
          capabilities: [{ capability: 'c1', input_artifact_types: [], output_artifact_types: [], status: 'native' }],
        },
      },
    ]);
    const caps = await getCapabilities();
    expect(caps.transform_ops).toEqual(['buffer']);
    expect(caps.capabilities).toHaveLength(1);
    expect(String(fn.mock.calls[0][0])).toContain('/api/v1/model-authoring/capabilities');
  });

  it('POST /projects/{pid}/models body 形状（model_slug + document）', async () => {
    const fn = stubFetch([
      {
        method: 'POST',
        path: '/api/v1/model-authoring/projects/p1/models',
        status: 200,
        body: { success: true, draft: { model_slug: 'm1', revision_no: 1, document: DOC } },
      },
    ]);
    const draft = await createModel('p1', { model_slug: 'm1', document: DOC });
    expect(draft.model_slug).toBe('m1');
    const init = fn.mock.calls[0][1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ model_slug: 'm1', document: DOC });
  });

  it('PUT CAS body 含 expected_revision；URL 编码 slug', async () => {
    const fn = stubFetch([
      {
        method: 'PUT',
        path: '/api/v1/model-authoring/projects/p1/models/m%2Fx',
        status: 200,
        body: { success: true, draft: { model_slug: 'm/x', revision_no: 2, document: DOC } },
      },
    ]);
    const draft = await updateModel('p1', 'm/x', { document: DOC, expected_revision: 2 });
    expect(draft.revision_no).toBe(2);
    const init = fn.mock.calls[0][1] as RequestInit;
    const body = JSON.parse(String(init.body));
    expect(body.expected_revision).toBe(2);
    expect(body.document).toEqual(DOC);
  });

  it('统一信封 409 REVISION_CONFLICT：typed code 读 body.data.code', async () => {
    stubFetch([
      {
        method: 'PUT',
        path: '/api/v1/model-authoring/projects/p1/models/m1',
        status: 409,
        body: {
          code: 'CONFLICT',
          success: false,
          message: '资源状态冲突',
          data: { code: 'REVISION_CONFLICT', detail: 'rev 1 stale' },
        },
      },
    ]);
    const err = await updateModel('p1', 'm1', { document: DOC, expected_revision: 1 }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ModelAuthoringApiError);
    expect((err as ModelAuthoringApiError).code).toBe('REVISION_CONFLICT');
    expect((err as ModelAuthoringApiError).message).toBe('rev 1 stale');
    expect((err as ModelAuthoringApiError).status).toBe(409);
  });

  it('发布 422 MODEL_INVALID：findings 随错误携带', async () => {
    const findings = [{ code: 'MODEL_CYCLE', severity: 'error', node_id: 'n1', edge: '', param: '', detail_zh: '环', detail_en: 'cycle', detail: 'cycle: n1' }];
    stubFetch([
      {
        method: 'POST',
        path: '/api/v1/model-authoring/projects/p1/models/m1/publish',
        status: 422,
        body: {
          code: 'UNPROCESSABLE',
          success: false,
          message: '请求参数校验失败',
          data: { code: 'MODEL_INVALID', detail: 'invalid', findings },
        },
      },
    ]);
    const err = await publishModel('p1', 'm1').catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ModelAuthoringApiError);
    expect((err as ModelAuthoringApiError).code).toBe('MODEL_INVALID');
    expect((err as ModelAuthoringApiError).findings).toHaveLength(1);
    expect((err as ModelAuthoringApiError).findings[0].code).toBe('MODEL_CYCLE');
  });

  it('validate 解包 valid/findings', async () => {
    stubFetch([
      {
        method: 'POST',
        path: '/api/v1/model-authoring/projects/p1/models/m1/validate',
        status: 200,
        body: { success: true, valid: true, findings: [], document_fingerprint: 'fp' },
      },
    ]);
    const out = await validateModel('p1', 'm1');
    expect(out.valid).toBe(true);
    expect(out.document_fingerprint).toBe('fp');
  });

  it('instantiate body 四键齐（session/version/param_values/input_bindings）', async () => {
    const fn = stubFetch([
      {
        method: 'POST',
        path: '/api/v1/model-authoring/projects/p1/models/m1/instantiate',
        status: 200,
        body: {
          success: true,
          model_slug: 'm1',
          package: { package_id: 'pkg', version: '1.0.0', fingerprint: 'fp' },
          params_resolved: {},
          node_params: {},
          bindings: {},
          instance: { instance_id: 'wi-1' },
        },
      },
    ]);
    const out = await instantiateModel('p1', 'm1', { version: '1.0.0', param_values: { distance: 300 } });
    expect(out.instance.instance_id).toBe('wi-1');
    expect(out.package.version).toBe('1.0.0');
    const body = JSON.parse(String((fn.mock.calls[0][1] as RequestInit).body));
    expect(body).toEqual({ session_id: '', version: '1.0.0', param_values: { distance: 300 }, input_bindings: {} });
  });

  it('run 走 /instances/{iid}/run?project_id= 查询参数', async () => {
    const fn = stubFetch([
      {
        method: 'POST',
        path: '/api/v1/model-authoring/instances/wi-1/run',
        status: 200,
        body: { success: true, run: { status: 'succeeded' }, node_params: {} },
      },
    ]);
    const out = await runModelInstance('wi-1', { projectId: 'p1', deadlineS: 30 });
    expect((out.run as { status: string }).status).toBe('succeeded');
    const url = String(fn.mock.calls[0][0]);
    expect(url).toContain('/instances/wi-1/run?project_id=p1');
    expect(JSON.parse(String((fn.mock.calls[0][1] as RequestInit).body))).toEqual({ deadline_s: 30 });
  });

  it('getModel 解包 draft', async () => {
    stubFetch([
      {
        method: 'GET',
        path: '/api/v1/model-authoring/projects/p1/models/m1',
        status: 200,
        body: { success: true, draft: { model_slug: 'm1', revision_no: 3, document: DOC } },
      },
    ]);
    const draft = await getModel('p1', 'm1');
    expect(draft.revision_no).toBe(3);
  });

  it('DRAFT_NOT_FOUND 404 折叠为 typed 错误', async () => {
    stubFetch([
      {
        method: 'GET',
        path: '/api/v1/model-authoring/projects/p1/models/zz',
        status: 404,
        body: { code: 'NOT_FOUND', success: false, message: '资源不存在', data: { code: 'DRAFT_NOT_FOUND', detail: 'zz' } },
      },
    ]);
    await expect(getModel('p1', 'zz')).rejects.toMatchObject({
      name: 'ModelAuthoringApiError',
      code: 'DRAFT_NOT_FOUND',
      status: 404,
    });
  });
});
