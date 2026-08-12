import { describe, it, expect } from 'vitest';
import { ApiError, ApiTimeoutError } from '@/lib/api/transport';
import type { RunComparison, WorkflowRunDetail } from '@/lib/api/project';
import {
  buildLineageRows,
  formatCrs,
  formatOutcomeMessage,
  isAbortError,
  isActiveRunStatus,
  isPartialRun,
  outcomeToastVariant,
  parseApiErrorDetail,
  runFingerprintsEqual,
  shouldOfferResume,
  summarizeCompare,
  unwrapPage,
} from './recovery';

function run(overrides: Partial<WorkflowRunDetail> = {}): WorkflowRunDetail {
  return {
    id: 'run_1',
    workflow_id: 'wf_1',
    workflow_version: 1,
    project_id: 'p1',
    workflow_revision_id: 'rev_1',
    input_bindings: {},
    input_dataset_fingerprints: {},
    status: 'failed',
    execution_trace: [],
    outputs: {},
    cost_perf_summary: {},
    completed_steps: ['s1'],
    created_at: '2026-08-13T00:00:00Z',
    ...overrides,
  };
}

describe('formatCrs (INV-ART1)', () => {
  it('never fabricates EPSG:4326 for null or empty CRS', () => {
    expect(formatCrs(null)).toBe('未知');
    expect(formatCrs(undefined)).toBe('未知');
    expect(formatCrs('')).toBe('未知');
    expect(formatCrs('   ')).toBe('未知');
  });

  it('passes through a truthful CRS string', () => {
    expect(formatCrs('EPSG:3857')).toBe('EPSG:3857');
  });
});

describe('partial / resume offer — backend-derived only', () => {
  it('treats failed + completed steps as partial, not as a backend status', () => {
    expect(isPartialRun(run({ status: 'failed', completed_steps: ['s1'] }))).toBe(true);
    expect(isPartialRun(run({ status: 'cancelled', completed_steps: ['s1'] }))).toBe(true);
    expect(isPartialRun(run({ status: 'completed', completed_steps: ['s1'] }))).toBe(false);
    expect(isPartialRun(run({ status: 'failed', completed_steps: [] }))).toBe(false);
  });

  it('offers resume only for failed/cancelled with completed steps — never as a fact of resumability', () => {
    expect(shouldOfferResume(run({ status: 'failed', completed_steps: ['s1'] }))).toBe(true);
    expect(shouldOfferResume(run({ status: 'cancelled', completed_steps: ['s1'] }))).toBe(true);
    expect(shouldOfferResume(run({ status: 'failed', completed_steps: [] }))).toBe(false);
    expect(shouldOfferResume(run({ status: 'completed', completed_steps: ['s1'] }))).toBe(false);
    expect(shouldOfferResume(run({ status: 'running', completed_steps: ['s1'] }))).toBe(false);
    expect(shouldOfferResume(null)).toBe(false);
    expect(shouldOfferResume({ status: 'failed' } as WorkflowRunDetail)).toBe(false);
  });
});

describe('formatOutcomeMessage — never claim success against backend status', () => {
  it('quotes the returned status instead of saying the action succeeded', () => {
    expect(formatOutcomeMessage('replay', run({ status: 'failed' }))).toBe(
      '回放结束，后端状态：失败',
    );
    expect(formatOutcomeMessage('resume', run({ status: 'failed' }))).toBe(
      '续跑结束，后端状态：失败',
    );
    expect(formatOutcomeMessage('replay', run({ status: 'completed' }))).toBe(
      '回放结束，后端状态：已完成',
    );
    expect(formatOutcomeMessage('run', run({ status: 'failed' }))).toBe(
      '运行结束，后端状态：失败',
    );
  });

  it('does not contain 成功 or 已提交 when the returned run is not completed', () => {
    const msg = formatOutcomeMessage('resume', run({ status: 'cancelled' }));
    expect(msg).not.toMatch(/成功/);
    expect(msg).not.toMatch(/已提交/);
    expect(msg).toContain('已取消');
  });

  it('maps toast chrome from run status, not from HTTP', () => {
    expect(outcomeToastVariant('failed')).toBe('error');
    expect(outcomeToastVariant('cancelled')).toBe('warning');
    expect(outcomeToastVariant('completed')).toBe('success');
    expect(outcomeToastVariant('running')).toBe('info');
  });
});

describe('runFingerprintsEqual', () => {
  it('uses only the backend same flag — never infers from display fields', () => {
    const cmp: RunComparison = {
      run_a_id: 'a',
      run_b_id: 'b',
      revision: {
        run_a_revision: 'r1',
        run_b_revision: 'r1',
        run_a_graph_fingerprint: 'g',
        run_b_graph_fingerprint: 'g',
        graph_same: true,
      },
      inputs_changed: { run_a: {}, run_b: {}, diff_keys: [] },
      dataset_versions_changed: { run_a: {}, run_b: {}, diff_keys: [] },
      tool_versions_changed: {},
      params_changed: {},
      output_artifacts_changed: {},
      metrics_changed: {},
      warnings_changed: {},
      run_fingerprint: { run_a: 'fp1', run_b: 'fp1', same: false },
    };
    expect(runFingerprintsEqual(cmp)).toBe(false);
    expect(runFingerprintsEqual({ ...cmp, run_fingerprint: { run_a: 'x', run_b: 'y', same: true } })).toBe(
      true,
    );
    expect(runFingerprintsEqual({ ...cmp, run_fingerprint: { run_a: null, run_b: null, same: false } })).toBe(
      false,
    );
    const missingFp: RunComparison = {
      ...cmp,
      revision: {
        ...cmp.revision,
        graph_same: true,
        run_a_graph_fingerprint: null,
        run_b_graph_fingerprint: null,
      },
      run_fingerprint: { run_a: null, run_b: null, same: false },
    };
    expect(runFingerprintsEqual(missingFp)).toBe(false);
  });
});

describe('summarizeCompare', () => {
  it('lists only sections the backend reports as changed', () => {
    const rows = summarizeCompare({
      run_a_id: 'a',
      run_b_id: 'b',
      revision: {
        run_a_revision: 'r1',
        run_b_revision: 'r2',
        run_a_graph_fingerprint: 'g1',
        run_b_graph_fingerprint: 'g2',
        graph_same: false,
      },
      inputs_changed: { run_a: { aoi: 'A' }, run_b: { aoi: 'B' }, diff_keys: ['aoi'] },
      dataset_versions_changed: { run_a: {}, run_b: {}, diff_keys: [] },
      tool_versions_changed: { buffer: ['1', '2'] },
      params_changed: {},
      output_artifacts_changed: { run_a_artifact_count: 1, run_b_artifact_count: 2 },
      metrics_changed: { run_a_perf: { ms: 1 }, run_b_perf: { ms: 2 } },
      warnings_changed: { run_a_error: 'x', run_b_error: null },
      run_fingerprint: { run_a: 'aa', run_b: 'bb', same: false },
    });
    const labels = rows.map((r) => r.label);
    expect(labels).toContain('工作流修订 / 图指纹');
    expect(labels).toContain('输入参数');
    expect(labels).toContain('工具版本');
    expect(labels).not.toContain('数据集版本');
  });
});

describe('parseApiErrorDetail', () => {
  it('surfaces FastAPI detail so a 409 reason is not swallowed', () => {
    const err = new ApiError(409, 'Conflict', {
      detail: 'cannot resume run r1: input dataset fingerprints changed',
    });
    expect(parseApiErrorDetail(err, 'fallback')).toContain('input dataset fingerprints changed');
  });

  it('does not treat abort as a user-facing error', () => {
    const abort = new DOMException('Aborted', 'AbortError');
    expect(isAbortError(abort)).toBe(true);
    expect(parseApiErrorDetail(abort, 'fallback')).toBe('fallback');
  });

  it('does not treat timeout as abort — the server may have created a run', () => {
    expect(isAbortError(new ApiTimeoutError(120000))).toBe(false);
  });
});

describe('unwrapPage', () => {
  it('reads items from the Page envelope and accepts a bare array for tests', () => {
    expect(unwrapPage({ items: [{ id: '1' }], total: 1, limit: 50, offset: 0, has_more: false })).toEqual([
      { id: '1' },
    ]);
    expect(unwrapPage([{ id: '2' }])).toEqual([{ id: '2' }]);
    expect(unwrapPage(null)).toEqual([]);
  });
});

describe('lineage rows', () => {
  it('builds a bounded accessible list and reports empty when the graph has no edges', () => {
    expect(buildLineageRows({ artifact_id: 'a1', parents: [], consumers: [] }, 32)).toEqual([]);
    const rows = buildLineageRows(
      {
        artifact_id: 'child',
        parents: [
          {
            lineage_id: 'l1',
            artifact_id: 'child',
            parent_artifact_id: 'parent',
            producing_tool: 'buffer',
            tool_version: '1.0',
            depth: 1,
            source_dataset_id: 'ds1',
            source_dataset_fingerprint: 'fp1',
          },
        ],
        consumers: [
          {
            lineage_id: 'l2',
            consumer_artifact_id: 'next',
            parent_artifact_id: 'child',
            producing_tool: 'clip',
            depth: 1,
          },
        ],
      },
      2,
    );
    expect(rows).toHaveLength(2);
    expect(rows[0].direction).toBe('upstream');
    expect(rows[1].direction).toBe('downstream');
  });
});

describe('active run status', () => {
  it('only pending/running are pollable', () => {
    expect(isActiveRunStatus('pending')).toBe(true);
    expect(isActiveRunStatus('running')).toBe(true);
    expect(isActiveRunStatus('failed')).toBe(false);
    expect(isActiveRunStatus('completed')).toBe(false);
  });
});
