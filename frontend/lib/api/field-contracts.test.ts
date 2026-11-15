/**
 * field-contracts API 客户端测试：路径构建（ref_id 含 ':' 的编码）、
 * findings 分页参数、错误信封提取（真实 apiFetch 面以 transport mock 隔离）。
 */
import { describe, expect, it, vi, beforeEach } from 'vitest';

const apiFetch = vi.hoisted(() => ({ apiFetch: vi.fn() }));
vi.mock('./transport', () => ({ apiFetch: apiFetch.apiFetch }));

import {
  asFieldContractError,
  bindingStatus,
  createContract,
  findingsPage,
  listContracts,
  observeFields,
  reviseContract,
  startAssessment,
} from './field-contracts';

beforeEach(() => {
  vi.clearAllMocks();
});

describe('路径构建', () => {
  it('listContracts 对含 : 的 ref_id 做查询参数编码', async () => {
    apiFetch.apiFetch.mockResolvedValue({ contracts: [] });
    await listContracts('sess-1', 'dataset:abc:def', 'tok');
    expect(apiFetch.apiFetch.mock.calls[0][0]).toBe(
      '/api/v1/chat/sessions/sess-1/field-contracts?ref_id=dataset%3Aabc%3Adef',
    );
    expect(apiFetch.apiFetch.mock.calls[0][1]).toMatchObject({ ownerToken: 'tok' });
  });

  it('observeFields / bindingStatus 对含 : 的 ref_id 路径段编码', async () => {
    apiFetch.apiFetch.mockResolvedValue({});
    await observeFields('sess-1', 'dataset:abc');
    expect(apiFetch.apiFetch.mock.calls[0][0]).toBe(
      '/api/v1/chat/sessions/sess-1/field-contracts/datasets/dataset%3Aabc/observe',
    );
    await bindingStatus('sess-1', 'dataset:abc');
    expect(apiFetch.apiFetch.mock.calls[1][0]).toBe(
      '/api/v1/chat/sessions/sess-1/field-contracts/datasets/dataset%3Aabc/binding',
    );
  });

  it('sessionId 也编码；listContracts 无 ref_id 时不带查询串', async () => {
    apiFetch.apiFetch.mockResolvedValue({ contracts: [] });
    await listContracts('se/ss');
    expect(apiFetch.apiFetch.mock.calls[0][0]).toBe(
      '/api/v1/chat/sessions/se%2Fss/field-contracts',
    );
  });
});

describe('findings 分页参数', () => {
  it('cursor/limit/code 组装为查询参数', async () => {
    apiFetch.apiFetch.mockResolvedValue({ findings: [], total_violations: 0, filtered_total: 0, next_cursor: null });
    await findingsPage('sess-1', 'asm-9', { cursor: 'c7', limit: 50, code: 'enum_violation' });
    expect(apiFetch.apiFetch.mock.calls[0][0]).toBe(
      '/api/v1/chat/sessions/sess-1/field-contracts-assessments/asm-9/findings?cursor=c7&limit=50&code=enum_violation',
    );
  });

  it('空查询不带 ?；next_cursor 透传到下一页', async () => {
    apiFetch.apiFetch.mockResolvedValue({
      findings: [{ code: 'required_missing' }],
      total_violations: 3,
      filtered_total: 3,
      next_cursor: 'cur-2',
    });
    const page = await findingsPage('sess-1', 'asm-9');
    expect(apiFetch.apiFetch.mock.calls[0][0]).toBe(
      '/api/v1/chat/sessions/sess-1/field-contracts-assessments/asm-9/findings',
    );
    expect(page.next_cursor).toBe('cur-2');
    await findingsPage('sess-1', 'asm-9', { cursor: page.next_cursor, limit: 20 });
    expect(apiFetch.apiFetch.mock.calls[1][0]).toBe(
      '/api/v1/chat/sessions/sess-1/field-contracts-assessments/asm-9/findings?cursor=cur-2&limit=20',
    );
  });
});

describe('写路径 body', () => {
  it('createContract POST {ref_id, document}；reviseContract PUT 带 expected_revision', async () => {
    const doc = { version: 1 as const, title: 't', fields: [{ field: 'name', type: 'string' as const, required: true, nullable: false, empty_string: 'reject' as const }], cross_rules: [] };
    apiFetch.apiFetch.mockResolvedValue({ contract_id: 'c1' });
    await createContract('sess-1', 'dataset:abc', doc);
    expect(apiFetch.apiFetch.mock.calls[0]).toMatchObject([
      '/api/v1/chat/sessions/sess-1/field-contracts',
      { method: 'POST', body: { ref_id: 'dataset:abc', document: doc } },
    ]);
    await reviseContract('sess-1', 'c1', 3, doc);
    expect(apiFetch.apiFetch.mock.calls[1]).toMatchObject([
      '/api/v1/chat/sessions/sess-1/field-contracts/c1',
      { method: 'PUT', body: { expected_revision: 3, document: doc } },
    ]);
    await startAssessment('sess-1', 'c1');
    expect(apiFetch.apiFetch.mock.calls[2]).toMatchObject([
      '/api/v1/chat/sessions/sess-1/field-contracts/c1/assess',
      { method: 'POST' },
    ]);
  });
});

describe('asFieldContractError', () => {
  it('提取 detail={code,message,detail} 信封（revision_conflict CAS）', () => {
    const err = {
      body: { detail: { code: 'revision_conflict', message: 'stale', detail: { current_revision: 4 } } },
    };
    expect(asFieldContractError(err)).toEqual({
      code: 'revision_conflict',
      message: 'stale',
      detail: { current_revision: 4 },
    });
  });

  it('统一 data 信封与未知形态兜底', () => {
    expect(
      asFieldContractError({ body: { data: { code: 'assessment_stale', message: 're-assess' } } }),
    ).toEqual({ code: 'assessment_stale', message: 're-assess', detail: undefined });
    expect(asFieldContractError(new Error('boom'))).toEqual({
      code: 'network_error',
      message: 'boom',
    });
  });
});
