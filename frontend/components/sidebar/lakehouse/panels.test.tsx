import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';

import {
  DATA_OBJECT_ID,
  DATASET_ID,
  OWNER_TOKEN,
  PROJECT_ID,
  SESSION_ID,
  VERSION_ID,
  makeGCPlan,
  makeLineageView,
  makeScrubReport,
  makeStacResult,
} from '@/test/lakehouse/fixtures';

/**
 * P5 STAC / P6 版本工作台 / P8 运维面板的组件契约：
 * - STAC：collection 元信息 + 条目展开（assets + 几何上图）+ skipped 披露；
 * - 版本工作台：publish 确认对话框 → 幂等报告；快照 diff 双栏字段；
 * - 运维：检视（血缘 + scrub）与 GC 只读纪律（有 plan 无 execute）。
 */

const lakehouseApi = vi.hoisted(() => ({
  searchCatalogStac: vi.fn(),
  publish: vi.fn(),
  revoke: vi.fn(),
  resolveDatasetVersion: vi.fn(),
  getObjectLineage: vi.fn(),
  scrubObject: vi.fn(),
  planGc: vi.fn(),
}));
vi.mock('@/lib/api/lakehouse', () => ({ lakehouseApi }));

const hudStore = { addLayer: vi.fn() };
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: Object.assign((selector: (s: typeof hudStore) => unknown) => selector(hudStore), {
    getState: () => hudStore,
  }),
}));

const toastStore = { addToast: vi.fn() };
vi.mock('@/components/ui/toast', () => ({
  useToastStore: (selector: (s: typeof toastStore) => unknown) => selector(toastStore),
}));

import { StacExplorer } from './stac-explorer';
import { VersionWorkbench } from './version-workbench';
import { OpsPanel } from './ops-panel';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('StacExplorer（P5）', () => {
  it('渲染 collection + 条目；条目展开披露 assets；几何上图挂 vector 层', async () => {
    lakehouseApi.searchCatalogStac.mockResolvedValue(makeStacResult(2));
    render(
      <StacExplorer ownerType="session" ownerId={SESSION_ID} sessionId={SESSION_ID} ownerToken={OWNER_TOKEN} />,
    );
    await waitFor(() => expect(screen.getByText('Collection')).toBeInTheDocument());
    expect(screen.getByText(/1\.0\.0/)).toBeInTheDocument();
    // 展开第一个条目（条目列表内的卡片按钮）。
    const list = screen.getByTestId('lakehouse-stac-items');
    const card = within(list).getAllByRole('button')[0];
    fireEvent.click(card);
    const detail = await screen.findByTestId('lakehouse-stac-detail');
    expect(detail).toBeInTheDocument();
    expect(screen.getByText('资产（assets）')).toBeInTheDocument();
    fireEvent.click(screen.getByText('几何上图'));
    await waitFor(() => expect(hudStore.addLayer).toHaveBeenCalled());
    const layer = hudStore.addLayer.mock.calls[0][0];
    expect(layer.type).toBe('vector');
  });

  it('skipped 条目诚实披露', async () => {
    lakehouseApi.searchCatalogStac.mockResolvedValue(makeStacResult(0, { skipped: ['f'.repeat(64)] }));
    render(<StacExplorer ownerType="session" ownerId={SESSION_ID} sessionId={SESSION_ID} />);
    await waitFor(() =>
      expect(screen.getByText(/无法投影/)).toBeInTheDocument(),
    );
  });

  it('下一页读取 links 里的 offset', async () => {
    lakehouseApi.searchCatalogStac.mockResolvedValue(
      makeStacResult(2, {
        collection: {
          type: 'Collection',
          stac_version: '1.0.0',
          id: 'c',
          description: 'd',
          license: 'proprietary',
          links: [
            { rel: 'root', href: './collection.json' },
            { rel: 'next', href: `./collection.json?offset=20&owner_id=${SESSION_ID}` },
          ],
        },
      }),
    );
    render(<StacExplorer ownerType="session" ownerId={SESSION_ID} sessionId={SESSION_ID} />);
    const next = await screen.findByRole('button', { name: '下一页' });
    fireEvent.click(next);
    await waitFor(() =>
      expect(lakehouseApi.searchCatalogStac).toHaveBeenLastCalledWith(
        expect.objectContaining({ offset: 20 }),
        expect.anything(),
      ),
    );
  });
});

describe('VersionWorkbench（P6）', () => {
  function renderWorkbench() {
    return render(
      <VersionWorkbench ownerType="session" sessionId={SESSION_ID} projectId={PROJECT_ID} ownerToken={OWNER_TOKEN} />,
    );
  }

  it('publish：确认对话框 → 幂等报告呈现', async () => {
    lakehouseApi.publish.mockResolvedValue({
      success: true,
      published: [{ object_id: DATA_OBJECT_ID, artifact_id: 'art_lh_x', revision_no: 1, revision_created: true, deduped: false }],
      unknown: [],
      forbidden: [],
    });
    renderWorkbench();
    fireEvent.change(screen.getByLabelText('对象 ID 列表'), { target: { value: DATA_OBJECT_ID } });
    fireEvent.click(screen.getByTestId('lakehouse-publish'));
    // 确认对话框（repo ConfirmDialog：焦点管理 + 确认按钮）。
    const confirm = await screen.findByRole('button', { name: '发布' });
    fireEvent.click(confirm);
    await waitFor(() => expect(lakehouseApi.publish).toHaveBeenCalledWith(
      { session_id: SESSION_ID, project_id: PROJECT_ID, object_ids: [DATA_OBJECT_ID] },
      expect.anything(),
    ));
    await waitFor(() => expect(screen.getByText(/发布 1 · 去重 0 · 未知 0 · 无权 0/)).toBeInTheDocument());
  });

  it('publish 权限不足 → 错误报告持久呈现（不只是 toast）', async () => {
    lakehouseApi.publish.mockRejectedValue(new Error('404'));
    renderWorkbench();
    fireEvent.change(screen.getByLabelText('对象 ID 列表'), { target: { value: DATA_OBJECT_ID } });
    fireEvent.click(screen.getByTestId('lakehouse-publish'));
    fireEvent.click(await screen.findByRole('button', { name: '发布' }));
    await waitFor(() => expect(screen.getByText('404')).toBeInTheDocument());
  });

  it('revoke：tombstone 报告', async () => {
    lakehouseApi.revoke.mockResolvedValue({ success: true, revoked: [DATA_OBJECT_ID], unknown: [] });
    renderWorkbench();
    fireEvent.change(screen.getByLabelText('对象 ID 列表'), { target: { value: DATA_OBJECT_ID } });
    fireEvent.click(screen.getByTestId('lakehouse-revoke'));
    fireEvent.click(await screen.findByRole('button', { name: '撤销发布' }));
    await waitFor(() => expect(screen.getByText(/已撤销 1/)).toBeInTheDocument());
  });

  it('快照对比：双版本 → diff 双栏（变更行高亮）', async () => {
    lakehouseApi.resolveDatasetVersion
      .mockResolvedValueOnce({
        success: true,
        version_id: VERSION_ID,
        parent_version_id: null,
        data_object_id: DATA_OBJECT_ID,
        content_sha256: 'd'.repeat(64),
        byte_size: 100,
        branch: 'main',
        action: 'commit',
        provenance: {},
        workflow_run_id: null,
        created_at: null,
        manifest: {},
        content_available: true,
        commit: { kind: 'dataset_commit' },
      })
      .mockResolvedValueOnce({
        success: true,
        version_id: 'e'.repeat(64),
        parent_version_id: VERSION_ID,
        data_object_id: DATA_OBJECT_ID,
        content_sha256: 'e'.repeat(64),
        byte_size: 200,
        branch: 'main',
        action: 'rollback',
        provenance: {},
        workflow_run_id: null,
        created_at: null,
        manifest: null,
        content_available: false,
        commit: { kind: 'dataset_commit' },
      });
    renderWorkbench();
    fireEvent.change(screen.getByLabelText('A 版本 dataset id'), { target: { value: DATASET_ID } });
    fireEvent.change(screen.getByLabelText('A 版本 version id'), { target: { value: VERSION_ID } });
    fireEvent.change(screen.getByLabelText('B 版本 dataset id'), { target: { value: DATASET_ID } });
    fireEvent.change(screen.getByLabelText('B 版本 version id'), { target: { value: 'e'.repeat(64) } });
    fireEvent.click(screen.getByTestId('lakehouse-diff-run'));
    await waitFor(() => expect(screen.getByTestId('lakehouse-diff-view')).toBeInTheDocument());
    // 变更行（byte_size 100 vs 200、action commit vs rollback）以高亮呈现。
    expect(screen.getByText('rollback')).toBeInTheDocument();
    expect(screen.getByTestId('lakehouse-diff-map')).toBeInTheDocument();
  });
});

describe('OpsPanel（P8，只读纪律）', () => {
  it('检视：血缘链（root 不在祖先）+ scrub 状态披露', async () => {
    lakehouseApi.getObjectLineage.mockResolvedValue(makeLineageView());
    lakehouseApi.scrubObject.mockResolvedValue(makeScrubReport());
    render(
      <OpsPanel sessionId={SESSION_ID} ownerToken={OWNER_TOKEN} lineageTarget={null} onTargetConsumed={() => {}} />,
    );
    fireEvent.change(screen.getByLabelText('对象 ID'), { target: { value: DATA_OBJECT_ID } });
    fireEvent.click(screen.getByTestId('lakehouse-ops-inspect'));
    await waitFor(() => expect(screen.getByTestId('lakehouse-lineage')).toBeInTheDocument());
    expect(screen.getByTestId('lakehouse-scrub-report')).toHaveTextContent('verified');
    expect(lakehouseApi.scrubObject).toHaveBeenCalledWith(
      DATA_OBJECT_ID,
      expect.objectContaining({ mode: 'sample' }),
      expect.anything(),
    );
  });

  it('scrub 损坏态渲染危险提示', async () => {
    lakehouseApi.getObjectLineage.mockResolvedValue(makeLineageView());
    lakehouseApi.scrubObject.mockResolvedValue(makeScrubReport({ state: 'corrupt', corrupt: ['blob-1'] }));
    render(
      <OpsPanel sessionId={SESSION_ID} lineageTarget={null} onTargetConsumed={() => {}} />,
    );
    fireEvent.change(screen.getByLabelText('对象 ID'), { target: { value: DATA_OBJECT_ID } });
    fireEvent.click(screen.getByTestId('lakehouse-ops-inspect'));
    await waitFor(() => expect(screen.getByTestId('lakehouse-scrub-report')).toHaveTextContent('corrupt'));
  });

  it('GC 只读纪律：有 dry-run 计划、无 execute 动作；计划候选有界展示', async () => {
    lakehouseApi.planGc.mockResolvedValue(
      makeGCPlan({ candidates: Array.from({ length: 10 }, (_, i) => `${i}`.repeat(64)) }),
    );
    render(
      <OpsPanel sessionId={SESSION_ID} lineageTarget={null} onTargetConsumed={() => {}} />,
    );
    fireEvent.click(screen.getByTestId('lakehouse-gc-plan'));
    await waitFor(() => expect(screen.getByTestId('lakehouse-gc-plan-view')).toBeInTheDocument());
    expect(screen.getByText(/共 10 个候选/)).toBeInTheDocument();
    // 只读纪律：全面板无 execute 触发。
    expect(screen.queryByRole('button', { name: /执行 GC/ })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /execute/i })).not.toBeInTheDocument();
  });

  it('GC 非 admin（403）→ 警告提示而非崩溃', async () => {
    lakehouseApi.planGc.mockRejectedValue(new Error('Admin privileges required'));
    render(
      <OpsPanel sessionId={SESSION_ID} lineageTarget={null} onTargetConsumed={() => {}} />,
    );
    fireEvent.click(screen.getByTestId('lakehouse-gc-plan'));
    await waitFor(() => expect(screen.getByText(/Admin privileges required/)).toBeInTheDocument());
  });
});
