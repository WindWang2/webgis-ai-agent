/**
 * useFieldContracts hook 单测（G44 工作台状态机）：
 * - 草稿保存 revision CAS 冲突（revision_conflict）typed 码如实上抛；
 * - findings 分页 cursor 前进/后退（next_cursor 透传，不猜偏移）；
 * - 激活闸门：violations>0 → 禁用；0 违规 + digest 一致 → 放开；
 *   评估后改规则（digest 不匹配）→ 继续禁用。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import type { FieldContractView } from '@/lib/types/field-contracts';

const fcApi = vi.hoisted(() => ({
  listContracts: vi.fn(),
  bindingStatus: vi.fn(),
  observeFields: vi.fn(),
  createContract: vi.fn(),
  reviseContract: vi.fn(),
  startAssessment: vi.fn(),
  getAssessment: vi.fn(),
  findingsPage: vi.fn(),
  activateContract: vi.fn(),
  retireContract: vi.fn(),
}));
vi.mock('@/lib/api/field-contracts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api/field-contracts')>();
  return { ...actual, ...fcApi };
});
vi.mock('@/lib/api/selection-handles', () => ({
  createSelectionHandle: vi.fn(),
  fetchSelectionFeatures: vi.fn(),
  materializeSelectionHandle: vi.fn(),
  releaseSelectionHandle: vi.fn(),
}));
vi.mock('@/lib/api/feature-edit', () => ({
  previewFeatureEdit: vi.fn(),
  decideFeatureEditPlan: vi.fn(),
}));
vi.mock('@/lib/edit/layer-edit-bridge', () => ({
  featureIdFromProperties: vi.fn((p: Record<string, unknown> | null) => (p?._wfid as string) ?? null),
}));
vi.mock('@/lib/selection/selection-store', () => ({
  getSelection: vi.fn(() => null),
}));

import { useFieldContracts } from './use-field-contracts';

const DOC = {
  version: 1 as const,
  title: 'demo',
  fields: [{ field: 'name', type: 'string' as const, required: true, nullable: false, empty_string: 'reject' as const }],
  cross_rules: [],
};

function contractView(overrides: Partial<FieldContractView> = {}): FieldContractView {
  return {
    contract_id: 'c1',
    contract_key: 'fc#1',
    session_id: 's1',
    ref_id: 'dataset:abc',
    status: 'draft',
    revision: 2,
    title: 'demo',
    rules: DOC,
    normalized_digest: 'digest-aaa',
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
  fcApi.bindingStatus.mockResolvedValue({ status: 'missing', contract: null });
  fcApi.observeFields.mockResolvedValue({
    ref_id: 'dataset:abc', feature_count: 3, observed: {}, declared: [], declared_source: null,
  });
  fcApi.listContracts.mockResolvedValue({ contracts: [] });
});

describe('草稿保存 + revision CAS', () => {
  it('无草稿 → createContract；有草稿 → reviseContract(expected_revision=当前 revision)', async () => {
    const draft = contractView();
    fcApi.listContracts.mockResolvedValue({ contracts: [draft] });
    const { result } = renderHook(() =>
      useFieldContracts({ sessionId: 's1', ownerToken: 'tok', refId: 'dataset:abc' }));
    await waitFor(() => expect(result.current.draft?.contract_id).toBe('c1'));

    fcApi.reviseContract.mockResolvedValue(contractView({ revision: 3 }));
    const saved = await act(() => result.current.saveDraft(DOC));
    expect(fcApi.reviseContract).toHaveBeenCalledWith('s1', 'c1', 2, DOC, 'tok');
    expect(fcApi.createContract).not.toHaveBeenCalled();
    expect(saved?.revision).toBe(3);
  });

  it('CAS 冲突（409 revision_conflict）→ 返回 null 且 typed 码上抛给 UI', async () => {
    const draft = contractView();
    fcApi.listContracts.mockResolvedValue({ contracts: [draft] });
    const { result } = renderHook(() =>
      useFieldContracts({ sessionId: 's1', ownerToken: null, refId: 'dataset:abc' }));
    await waitFor(() => expect(result.current.draft).toBeTruthy());

    fcApi.reviseContract.mockRejectedValue({
      body: { detail: { code: 'revision_conflict', message: 'stale revision', detail: { current_revision: 5 } } },
    });
    const saved = await act(() => result.current.saveDraft(DOC));
    expect(saved).toBeNull();
    expect(result.current.error?.code).toBe('revision_conflict');
    expect(result.current.error?.message).toBe('stale revision');
  });
});

describe('findings 分页 cursor', () => {
  it('评估完成后加载首页；next_cursor 前进 / 后退显式记录', async () => {
    const draft = contractView({ normalized_digest: 'digest-aaa' });
    fcApi.listContracts.mockResolvedValue({ contracts: [draft] });
    fcApi.startAssessment.mockResolvedValue({
      assessment: {
        id: 'asm-1', status: 'completed',
        verified: { rules_digest: 'digest-aaa' },
        counts: { violations_total: 3, checked_features: 3, total_features: 3, counts_by_code: { required_missing: 3 } },
      },
      job_id: 9, poll: '/api/v1/tasks/jobs/9',
    });
    fcApi.findingsPage.mockImplementation(async (_s: string, _id: string, q: { cursor?: string | null }) => ({
      findings: [{ code: 'required_missing', field: 'name', wfid: 'w1' }],
      total_violations: 3,
      filtered_total: 3,
      next_cursor: q.cursor === null ? 'cur-2' : null,
    }));
    const { result } = renderHook(() =>
      useFieldContracts({ sessionId: 's1', refId: 'dataset:abc' }));
    await waitFor(() => expect(result.current.draft).toBeTruthy());

    await act(async () => { await result.current.runAssessment('c1'); });
    // 首页（cursor null）
    expect(fcApi.findingsPage).toHaveBeenCalledWith('s1', 'asm-1', { cursor: null, limit: 20, code: null }, undefined);
    expect(result.current.findings?.next_cursor).toBe('cur-2');

    // 前进：next_cursor 透传
    await act(async () => { await result.current.nextFindingsPage(); });
    expect(fcApi.findingsPage).toHaveBeenLastCalledWith('s1', 'asm-1', { cursor: 'cur-2', limit: 20, code: null }, undefined);
    expect(result.current.hasPrevFindingsPage).toBe(true);

    // 后退：回到首页 cursor
    await act(async () => { await result.current.prevFindingsPage(); });
    expect(fcApi.findingsPage).toHaveBeenLastCalledWith('s1', 'asm-1', { cursor: null, limit: 20, code: null }, undefined);
    expect(result.current.hasPrevFindingsPage).toBe(false);
  });
});

describe('激活闸门', () => {
  it('violations>0 → canActivate=false + gate 带 count；0 违规 + digest 一致 → 放开', async () => {
    const draft = contractView({ normalized_digest: 'digest-aaa' });
    fcApi.listContracts.mockResolvedValue({ contracts: [draft] });
    fcApi.startAssessment.mockResolvedValue({
      assessment: {
        id: 'asm-1', status: 'completed',
        verified: { rules_digest: 'digest-aaa' },
        counts: { violations_total: 5 },
      },
    });
    const { result } = renderHook(() =>
      useFieldContracts({ sessionId: 's1', refId: 'dataset:abc' }));
    await waitFor(() => expect(result.current.draft).toBeTruthy());
    expect(result.current.canActivate).toBe(false); // 尚无评估

    await act(async () => { await result.current.runAssessment('c1'); });
    expect(result.current.canActivate).toBe(false);
    expect(result.current.activateGateKey).toBe('activate.gateViolations');
    expect(result.current.activateGateParams).toEqual({ count: 5 });

    // 重评：0 违规 + digest 匹配 → 闸门放开
    fcApi.startAssessment.mockResolvedValue({
      assessment: {
        id: 'asm-2', status: 'completed',
        verified: { rules_digest: 'digest-aaa' },
        counts: { violations_total: 0 },
      },
    });
    await act(async () => { await result.current.runAssessment('c1'); });
    expect(result.current.canActivate).toBe(true);
    expect(result.current.activateGateKey).toBeNull();
  });

  it('评估后规则修改（digest 不匹配）→ 仍禁用（gateDigestMismatch）', async () => {
    const draft = contractView({ normalized_digest: 'digest-bbb' }); // 保存后 digest 变了
    fcApi.listContracts.mockResolvedValue({ contracts: [draft] });
    fcApi.startAssessment.mockResolvedValue({
      assessment: {
        id: 'asm-1', status: 'completed',
        verified: { rules_digest: 'digest-aaa' }, // 评估时还是旧 digest
        counts: { violations_total: 0 },
      },
    });
    const { result } = renderHook(() =>
      useFieldContracts({ sessionId: 's1', refId: 'dataset:abc' }));
    await waitFor(() => expect(result.current.draft).toBeTruthy());
    await act(async () => { await result.current.runAssessment('c1'); });
    expect(result.current.canActivate).toBe(false);
    expect(result.current.activateGateKey).toBe('activate.gateDigestMismatch');
    // 0 违规也不放宽
    expect(result.current.assessment?.counts?.violations_total).toBe(0);
  });
});
