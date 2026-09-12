import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { clearCache } from './get-fast-path';

/**
 * Lakehouse typed client（P1）：29 端点逐一契约测试。
 * 模式与 lib/api/project.test.ts 相同 —— vi.stubGlobal('fetch') + jsonOk/jsonErr。
 * 断言面：URL（/api/v1 前缀 + 路径参数编码）、query、body 字段（所有权
 * 守卫必需的 session_id 留在 body）、响应透传、错误 → ApiError.status。
 */
const mockFetch = vi.fn();
vi.stubGlobal('fetch', mockFetch);

const jsonOk = (body: unknown, status = 200) => ({
  ok: true,
  status,
  statusText: 'OK',
  headers: { get: () => null },
  text: () => Promise.resolve(JSON.stringify(body)),
});

const jsonErr = (status: number, body: unknown) => ({
  ok: false,
  status,
  statusText: 'Error',
  headers: { get: () => null },
  text: () => Promise.resolve(JSON.stringify(body)),
});

import {
  lakehouseApi,
  type CubeWindowResult,
  type LabeledWindowResult,
  type VectorScanResult,
} from './lakehouse';
import { ApiError } from './transport';
import {
  DATA_OBJECT_ID,
  DATASET_ID,
  ERROR_STATES,
  OWNER_TOKEN,
  PROJECT_ID,
  SESSION_ID,
  VERSION_ID,
  apiErrorDetail,
  emptyCatalogPage,
  makeCatalogPage,
  makeCommitRecord,
  makeCubeBuildResult,
  makeCubeRevisionResult,
  makeCubeWindowResult,
  makeDataset,
  makeDatasetDetail,
  makeDatasetLineage,
  makeDatasetRef,
  makeDatasetVersion,
  makeGCExecuteResult,
  makeGCPlan,
  makeLabeledWindowResult,
  makeLineageView,
  makeManifest,
  makeObjectRead,
  makePublishResult,
  makeRevokeResult,
  makeRetentionExecuteResult,
  makeRetentionPlan,
  makeRsCubeBuildResult,
  makeScrubReport,
  makeScanResult,
  makeStacResult,
  makeVerifyResult,
} from '@/test/lakehouse/fixtures';

beforeEach(() => {
  vi.clearAllMocks();
  clearCache();
});

afterEach(() => {
  clearCache();
});

function lastCall(): { url: string; init: RequestInit } {
  const call = mockFetch.mock.calls[mockFetch.mock.calls.length - 1];
  return { url: String(call?.[0]), init: call?.[1] as RequestInit };
}

function lastBody(): Record<string, unknown> {
  return JSON.parse(String(lastCall().init.body));
}

/* ── 对象面 ───────────────────────────────────────────────────────────── */

describe('objects — read / verify / scrub / lineage', () => {
  it('getObject: 编码对象 id + query session_id', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeObjectRead()));
    const res = await lakehouseApi.getObject(DATA_OBJECT_ID, SESSION_ID, { ownerToken: OWNER_TOKEN });
    expect(res.manifest).toEqual(makeManifest());
    expect(lastCall().url).toContain(`/api/v1/lakehouse/objects/${DATA_OBJECT_ID}`);
    expect(lastCall().url).toContain(`session_id=${SESSION_ID}`);
  });

  it('getObject: 404 fail-closed', async () => {
    mockFetch.mockResolvedValueOnce(jsonErr(ERROR_STATES.notFound.status, ERROR_STATES.notFound.body));
    await expect(lakehouseApi.getObject(DATA_OBJECT_ID, SESSION_ID)).rejects.toMatchObject({
      status: 404,
    });
  });

  it('verifyObject: POST body 携带 session_id', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeVerifyResult()));
    const res = await lakehouseApi.verifyObject(DATA_OBJECT_ID, { session_id: SESSION_ID });
    expect(res.state).toBe('verified');
    expect(lastCall().init.method).toBe('POST');
    expect(lastBody().session_id).toBe(SESSION_ID);
    expect(lastCall().url).toContain('/verify');
  });

  it('scrubObject: sample 模式默认参数透传', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeScrubReport()));
    const res = await lakehouseApi.scrubObject(DATA_OBJECT_ID, {
      session_id: SESSION_ID,
      mode: 'sample',
      sample_k: 8,
    });
    expect(res.state).toBe('verified');
    expect(lastBody().mode).toBe('sample');
  });

  it('scrubReport: corrupt 态披露损坏块', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk(makeScrubReport({ state: 'corrupt', corrupt: ['blob-1'], chunks_checked: 8 })),
    );
    const res = await lakehouseApi.scrubObject(DATA_OBJECT_ID, { session_id: SESSION_ID });
    expect(res.state).toBe('corrupt');
    expect(res.corrupt).toEqual(['blob-1']);
  });

  it('getObjectLineage: root 不在 ancestors、edges 双节点', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeLineageView()));
    const view = await lakehouseApi.getObjectLineage(DATA_OBJECT_ID, SESSION_ID);
    expect(view.root).toBe(DATA_OBJECT_ID);
    expect(view.ancestors).toHaveLength(2);
    expect(view.ancestors.map((n) => n.id)).not.toContain(view.root);
    expect(view.edges[0]).toEqual({ child: DATA_OBJECT_ID, parent: '6'.repeat(64) });
  });

  it('getProjectObject: 项目域对象解析', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({ success: true, manifest: makeManifest(), catalog: makeCatalogPage(1).items[0] }),
    );
    const res = await lakehouseApi.getProjectObject(PROJECT_ID, DATA_OBJECT_ID);
    expect(lastCall().url).toContain(`/api/v1/lakehouse/projects/${PROJECT_ID}/objects/${DATA_OBJECT_ID}`);
    expect(res.manifest.kind).toBe('zarr_cube');
  });
});

describe('vector scan', () => {
  it('scanVector: GeoJSON FeatureCollection + session_id 留在 body', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeScanResult()));
    const res: VectorScanResult = await lakehouseApi.scanVector({
      session_id: SESSION_ID,
      ref: 'ref:fabric-parquet/abc',
      bbox: [116, 39, 117, 40],
      max_rows: 1000,
    });
    expect(res.type).toBe('FeatureCollection');
    expect(res.properties.truncated).toBe(false);
    expect(lastCall().url).toContain('/api/v1/lakehouse/vector/scan');
    expect(lastBody().session_id).toBe(SESSION_ID);
    expect(lastBody().max_rows).toBe(1000);
  });

  it('scanVector: ref 缺失 → 404（typed code 不出后端，只有 detail）', async () => {
    mockFetch.mockResolvedValueOnce(jsonErr(404, apiErrorDetail('lakehouse ref missing')));
    const err = await lakehouseApi
      .scanVector({ session_id: SESSION_ID, ref: 'ref:fabric-parquet/gone', bbox: [0, 0, 1, 1] })
      .catch((e) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err.status).toBe(404);
    expect(err.body).toEqual({ detail: 'lakehouse ref missing' });
  });
});

/* ── Cube 面 ──────────────────────────────────────────────────────────── */

describe('cubes — build / window / revise / rs / labeled window', () => {
  it('buildCube: durable 披露平铺在顶层', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeCubeBuildResult()));
    const res = await lakehouseApi.buildCube({
      session_id: SESSION_ID,
      title: '测试 cube',
      time_sources: [{ time: '2026-01-01', source: 'ref:fabric-parquet/a' }],
    });
    expect(res.ref).toBe('ref:cube/0123456789abcdef');
    expect(res.published).toBe(true);
    expect(res.durable).toBe('published');
    expect(lastBody().time_sources).toHaveLength(1);
  });

  it('buildCube: published=false 降级只带 reason', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({
        ...makeCubeBuildResult(),
        published: false,
        reason: 'manifest store unavailable',
        durable: undefined,
        data_object_id: undefined,
      }),
    );
    const res = await lakehouseApi.buildCube({
      session_id: SESSION_ID,
      time_sources: [{ time: '2026-01-01', source: 'x' }],
    });
    expect(res.published).toBe(false);
    expect(res.reason).toBe('manifest store unavailable');
    expect(res.data_object_id).toBeUndefined();
  });

  it('readCubeWindow: 切片参数进 body', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeCubeWindowResult()));
    const res: CubeWindowResult = await lakehouseApi.readCubeWindow({
      session_id: SESSION_ID,
      ref: 'ref:cube/0123456789abcdef',
      time: [0, 1],
      y: [0, 2],
      x: [0, 2],
    });
    expect(res.bands.band_0).toHaveLength(1);
    expect(res.nodata).toBe(-9999);
    expect(lastBody().time).toEqual([0, 1]);
  });

  it('readCubeWindow: 全无切片 → 422 路由前置校验', async () => {
    mockFetch.mockResolvedValueOnce(jsonErr(ERROR_STATES.unprocessable.status, ERROR_STATES.unprocessable.body));
    await expect(
      lakehouseApi.readCubeWindow({ session_id: SESSION_ID, ref: 'ref:cube/x' }),
    ).rejects.toMatchObject({ status: 422 });
  });

  it('reviseCube: CoW fork 披露 revision_of', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeCubeRevisionResult()));
    const res = await lakehouseApi.reviseCube({
      session_id: SESSION_ID,
      ref: 'ref:cube/0123456789abcdef',
      updates: [{ band: 'band_0', time_index: 0, source: 'ref:fabric-parquet/b' }],
    });
    expect(res.revision_of).toBe('ref:cube/0123456789abcdef');
    expect(res.first_step_preview.band_0).toBeDefined();
    expect(lastCall().url).toContain('/cubes/revise');
  });

  it('buildRsCube: labeled_projection + 变量清单', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeRsCubeBuildResult()));
    const res = await lakehouseApi.buildRsCube({
      session_id: SESSION_ID,
      sources: [
        { time: '2026-01-01', source: 'ref:fabric-parquet/s2', role: 'optical', band: 'B04' },
        { time: '2026-01-01', source: 'ref:fabric-parquet/s1', role: 'sar', polarization: 'VV' },
      ],
    });
    expect(res.variables).toContain('reflectance');
    expect(res.labeled_projection.dims[0]).toBe('time');
  });

  it('readLabeledWindow: model/scenario 维度进 body', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeLabeledWindowResult()));
    const res: LabeledWindowResult = await lakehouseApi.readLabeledWindow({
      session_id: SESSION_ID,
      ref: 'ref:cube/0123456789abcdef',
      model: ['ecmwf'],
      scenario: ['rcp45'],
      bbox: [116, 39, 117, 40],
    });
    expect(res.selection_plan.cells).toBe(4);
    expect(res.attrs.nodata_per_variable).toEqual({ temperature: -9999 });
    expect(lastBody().model).toEqual(['ecmwf']);
    expect(lastBody().scenario).toEqual(['rcp45']);
  });

  it('readLabeledWindow: 全空选择 → 422', async () => {
    mockFetch.mockResolvedValueOnce(jsonErr(ERROR_STATES.unprocessable.status, ERROR_STATES.unprocessable.body));
    await expect(
      lakehouseApi.readLabeledWindow({ session_id: SESSION_ID, ref: 'ref:cube/x' }),
    ).rejects.toMatchObject({ status: 422 });
  });
});

/* ── 发布 / 撤销 ──────────────────────────────────────────────────────── */

describe('publish / revoke', () => {
  it('publish: unknown/forbidden 披露', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makePublishResult()));
    const res = await lakehouseApi.publish({
      session_id: SESSION_ID,
      project_id: PROJECT_ID,
      object_ids: [DATA_OBJECT_ID],
      tags: ['demo'],
    });
    expect(res.published[0].artifact_id).toMatch(/^art_lh_/);
    expect(res.unknown).toEqual([]);
    expect(lastBody().tags).toEqual(['demo']);
  });

  it('publish: 项目不存在 → 404', async () => {
    mockFetch.mockResolvedValueOnce(jsonErr(404, apiErrorDetail('project not found')));
    await expect(
      lakehouseApi.publish({ session_id: SESSION_ID, project_id: 'gone', object_ids: [DATA_OBJECT_ID] }),
    ).rejects.toMatchObject({ status: 404 });
  });

  it('revoke: 请求体无 session_id', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeRevokeResult()));
    const res = await lakehouseApi.revoke({ project_id: PROJECT_ID, object_ids: [DATA_OBJECT_ID] });
    expect(res.revoked).toEqual([DATA_OBJECT_ID]);
    expect(lastBody().session_id).toBeUndefined();
    expect(lastBody().project_id).toBe(PROJECT_ID);
  });
});

/* ── Catalog / STAC ───────────────────────────────────────────────────── */

describe('catalog / stac', () => {
  it('searchCatalog: query 全参数 + total 字符串形态', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk(makeCatalogPage(2, { total: '>=10000', total_bounded: false, next_offset: 50 })),
    );
    const page = await lakehouseApi.searchCatalog({
      owner_type: 'session',
      owner_id: SESSION_ID,
      session_id: SESSION_ID,
      kind: 'zarr_cube',
      include_revoked: false,
      limit: 50,
      offset: 0,
    });
    expect(page.total).toBe('>=10000');
    expect(page.next_offset).toBe(50);
    const url = lastCall().url;
    expect(url).toContain('/api/v1/lakehouse/catalog?');
    expect(url).toContain('owner_type=session');
    expect(url).toContain('kind=zarr_cube');
  });

  it('searchCatalog: owner_type 非法 → 400', async () => {
    mockFetch.mockResolvedValueOnce(jsonErr(400, apiErrorDetail('owner_type must be session|project')));
    await expect(
      lakehouseApi.searchCatalog({ owner_type: 'tenant', owner_id: 'x' } as never),
    ).rejects.toMatchObject({ status: 400 });
  });

  it('searchCatalogStac: 返回 {collection, items, skipped} 包装', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeStacResult(2)));
    const res = await lakehouseApi.searchCatalogStac({
      owner_type: 'session',
      owner_id: SESSION_ID,
      session_id: SESSION_ID,
      limit: 20,
    });
    expect(res.collection.type).toBe('Collection');
    expect(res.collection.stac_version).toBe('1.0.0');
    expect(res.items).toHaveLength(2);
    expect(res.items[0].properties.datetime).toContain('Z');
  });

  it('searchCatalogStac: 不可投影条目进 skipped', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeStacResult(0, { skipped: ['f'.repeat(64)] })));
    const res = await lakehouseApi.searchCatalogStac({ owner_type: 'project', owner_id: PROJECT_ID });
    expect(res.items).toHaveLength(0);
    expect(res.skipped).toHaveLength(1);
  });
});

/* ── GC ──────────────────────────────────────────────────────────────── */

describe('gc — plan / execute', () => {
  it('planGc: token + 候选清单（无字节量字段）', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeGCPlan()));
    const plan = await lakehouseApi.planGc({ session_id: SESSION_ID, grace_hours: 48 });
    expect(plan.token).toHaveLength(64);
    expect(plan.candidates).toHaveLength(1);
    expect(plan).not.toHaveProperty('reclaimable_bytes');
  });

  it('planGc: 非 admin → 403', async () => {
    mockFetch.mockResolvedValueOnce(jsonErr(ERROR_STATES.forbidden.status, ERROR_STATES.forbidden.body));
    await expect(lakehouseApi.planGc({ session_id: SESSION_ID })).rejects.toMatchObject({ status: 403 });
  });

  it('executeGc: 透传 plan 对象；漂移 → 409', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeGCExecuteResult()));
    const plan = makeGCPlan();
    const res = await lakehouseApi.executeGc({ session_id: SESSION_ID, plan: plan as unknown as Record<string, unknown> });
    expect(res.deleted_manifests).toHaveLength(1);
    expect(lastBody().plan).toBeDefined();

    mockFetch.mockResolvedValueOnce(jsonErr(ERROR_STATES.conflict.status, ERROR_STATES.conflict.body));
    await expect(
      lakehouseApi.executeGc({ session_id: SESSION_ID, plan: { token: 'stale' } }),
    ).rejects.toMatchObject({ status: 409 });
  });
});

/* ── Dataset 版本层 ───────────────────────────────────────────────────── */

describe('datasets — 注册 / 清单 / 详情 / 版本', () => {
  it('createDataset: cube_contract 透传', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ success: true, created: true, dataset: makeDataset() }));
    const res = await lakehouseApi.createDataset({
      session_id: SESSION_ID,
      name: '气温观测',
      cube_contract: { dims: ['time', 'y', 'x'] },
    });
    expect(res.created).toBe(true);
    expect(res.dataset.dataset_id).toBe(DATASET_ID);
    expect(lastBody().name).toBe('气温观测');
  });

  it('listDatasets: count 与清单一致', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({ success: true, datasets: [makeDataset()], count: 1 }),
    );
    const res = await lakehouseApi.listDatasets(SESSION_ID, 50);
    expect(res.count).toBe(1);
    expect(lastCall().url).toContain('/api/v1/lakehouse/datasets?');
  });

  it('getDataset: refs + head + descriptor', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeDatasetDetail()));
    const res = await lakehouseApi.getDataset(DATASET_ID, SESSION_ID);
    expect(res.head?.ref_name).toBe('main');
    expect(res.refs.some((r) => r.ref_type === 'tag')).toBe(true);
    expect(res.descriptor?.kind).toBe('dataset_descriptor');
  });

  it('listDatasetVersions: branch 过滤', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({ success: true, versions: [makeDatasetVersion()], count: 1 }),
    );
    const res = await lakehouseApi.listDatasetVersions(DATASET_ID, SESSION_ID, 'main', 50);
    expect(lastCall().url).toContain('branch=main');
    expect(res.versions[0].action).toBe('commit');
  });

  it('resolveDatasetVersion: VersionRow 平铺 + manifest 三键', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({
        ...makeDatasetVersion(),
        manifest: makeManifest(),
        content_available: true,
        commit: makeCommitRecord(),
      }),
    );
    const res = await lakehouseApi.resolveDatasetVersion(DATASET_ID, VERSION_ID, SESSION_ID);
    expect(res.version_id).toBe(VERSION_ID);
    expect(res.commit?.kind).toBe('dataset_commit');
    expect(res.manifest?.kind).toBe('zarr_cube');
  });

  it('resolveDatasetVersion: 版本不存在 → 404', async () => {
    mockFetch.mockResolvedValueOnce(jsonErr(404, apiErrorDetail('dataset version not found')));
    await expect(
      lakehouseApi.resolveDatasetVersion(DATASET_ID, 'gone', SESSION_ID),
    ).rejects.toMatchObject({ status: 404 });
  });
});

describe('datasets — commit / branches / tags / rollback / lineage / retention', () => {
  it('commitDatasetVersion: CommitResult 全键', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({
        success: true,
        version_id: VERSION_ID,
        parent_version_id: null,
        data_object_id: DATA_OBJECT_ID,
        branch: 'main',
        generation: 1,
        version_deduped: false,
        ref_moved: true,
      }),
    );
    const res = await lakehouseApi.commitDatasetVersion(DATASET_ID, {
      session_id: SESSION_ID,
      branch: 'main',
      data_object_id: DATA_OBJECT_ID,
      action: 'commit',
      provenance: { algorithm: 'build_cube' },
    });
    expect(res.ref_moved).toBe(true);
    expect(lastBody().provenance).toEqual({ algorithm: 'build_cube' });
  });

  it('createDatasetBranch: 幂等 created 披露', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk({ success: true, created: false, ref: makeDatasetRef() }));
    const res = await lakehouseApi.createDatasetBranch(DATASET_ID, {
      session_id: SESSION_ID,
      name: 'dev',
    });
    expect(res.created).toBe(false);
    expect(res.ref.ref_name).toBe('main');
  });

  it('createDatasetTag: 重复 tag → 409', async () => {
    mockFetch.mockResolvedValueOnce(jsonErr(ERROR_STATES.conflict.status, ERROR_STATES.conflict.body));
    await expect(
      lakehouseApi.createDatasetTag(DATASET_ID, {
        session_id: SESSION_ID,
        name: 'v1',
        version_id: VERSION_ID,
      }),
    ).rejects.toMatchObject({ status: 409 });
  });

  it('rollbackDataset: 返回 CommitResult（action=rollback）', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk({
        success: true,
        version_id: 'e'.repeat(64),
        parent_version_id: VERSION_ID,
        data_object_id: DATA_OBJECT_ID,
        branch: 'main',
        generation: 2,
        version_deduped: false,
        ref_moved: true,
      }),
    );
    const res = await lakehouseApi.rollbackDataset(DATASET_ID, {
      session_id: SESSION_ID,
      branch: 'main',
      to_version_id: VERSION_ID,
    });
    expect(res.version_id).toBe('e'.repeat(64));
  });

  it('getDatasetLineage: chain + truncated 披露', async () => {
    mockFetch.mockResolvedValueOnce(
      jsonOk(makeDatasetLineage({ truncated: true, depth: 64 })),
    );
    const res = await lakehouseApi.getDatasetLineage(DATASET_ID, {
      session_id: SESSION_ID,
      version_id: VERSION_ID,
      max_depth: 64,
    });
    expect(res.chain).toHaveLength(2);
    expect(res.truncated).toBe(true);
  });

  it('planRetention: 保护披露（refs/window）', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeRetentionPlan()));
    const res = await lakehouseApi.planRetention(DATASET_ID, {
      session_id: SESSION_ID,
      max_versions: 64,
      min_age_hours: 72,
    });
    expect(res.candidate_count).toBe(1);
    expect(res.protected_by_refs).toBe(1);
  });

  it('executeRetention: pruned 清单；漂移 → 409', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeRetentionExecuteResult()));
    const res = await lakehouseApi.executeRetention(DATASET_ID, {
      session_id: SESSION_ID,
      plan: makeRetentionPlan() as unknown as Record<string, unknown>,
    });
    expect(res.pruned_count).toBe(1);

    mockFetch.mockResolvedValueOnce(jsonErr(ERROR_STATES.conflict.status, ERROR_STATES.conflict.body));
    await expect(
      lakehouseApi.executeRetention(DATASET_ID, { session_id: SESSION_ID, plan: { token: 'stale' } }),
    ).rejects.toMatchObject({ status: 409 });
  });
});

/* ── 通用传输语义 ─────────────────────────────────────────────────────── */

describe('transport 语义', () => {
  it('ownerToken → X-Session-Token 头（SEC-08 匿名会话）', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(makeCatalogPage(0)));
    await lakehouseApi.searchCatalog(
      { owner_type: 'session', owner_id: SESSION_ID, session_id: SESSION_ID },
      { ownerToken: OWNER_TOKEN },
    );
    const headers = lastCall().init.headers as Record<string, string>;
    expect(headers['X-Session-Token']).toBe(OWNER_TOKEN);
  });

  it('GET 走 fast path：同参短窗口内只打一次网络', async () => {
    mockFetch.mockResolvedValue(jsonOk(makeCatalogPage(0)));
    await lakehouseApi.listDatasets(SESSION_ID);
    await lakehouseApi.listDatasets(SESSION_ID);
    expect(mockFetch).toHaveBeenCalledTimes(1);
  });

  it('空 catalog 页（空态契约）', async () => {
    mockFetch.mockResolvedValueOnce(jsonOk(emptyCatalogPage));
    const page = await lakehouseApi.searchCatalog({ owner_type: 'session', owner_id: SESSION_ID });
    expect(page.items).toEqual([]);
    expect(page.next_offset).toBeNull();
  });
});
