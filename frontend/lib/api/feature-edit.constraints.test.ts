import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiFetch = vi.fn(async (_path: string, _init?: { method?: string; body?: unknown }) => ({}));
vi.mock('@/lib/api/transport', () => ({ apiFetch: (...args: unknown[]) => apiFetch(...(args as [string, { method?: string; body?: unknown }])) }));

import {
  getFeatureEditImpact,
  inspectFeatureEditConstraints,
  revalidateFeatureEditConstraints,
} from './feature-edit';

beforeEach(() => {
  apiFetch.mockClear();
});

describe('feature-edit constraint surface client（ADR-0300 只读三端点）', () => {
  it('constraints/inspect：POST 路径 + 可选过滤体（缺省空体）', async () => {
    await inspectFeatureEditConstraints('s1', 'ref:a', {
      toleranceM: 0.5,
      levelField: 'level',
      wfids: ['wf1', 'wf2'],
    });
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe('/api/v1/chat/sessions/s1/feature-edits/ref%3Aa/constraints/inspect');
    expect(init?.method).toBe('POST');
    expect(init?.body).toEqual({ tolerance_m: 0.5, level_field: 'level', wfids: ['wf1', 'wf2'] });

    await inspectFeatureEditConstraints('s1', 'ref:a');
    expect(apiFetch.mock.calls[1][1]?.body).toEqual({});
  });

  it('impact：POST ops + feature_ids（snake_case 线格式）', async () => {
    const ops = [
      { op: 'move_shared_vertex' as const, at: [104.0, 30.5] as [number, number], to: [104.1, 30.5] as [number, number] },
    ];
    await getFeatureEditImpact('s1', 'ref:a', { ops, featureIds: ['wf9'], toleranceM: 0 });
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe('/api/v1/chat/sessions/s1/feature-edits/ref%3Aa/impact');
    expect(init?.method).toBe('POST');
    expect(init?.body).toEqual({ ops, feature_ids: ['wf9'], tolerance_m: 0 });
  });

  it('impact：缺省 feature_ids/容差时不带冗余键', async () => {
    const ops = [{ op: 'replace_geometry' as const, geometry: { type: 'Point', coordinates: [0, 0] } as GeoJSON.Point }];
    await getFeatureEditImpact('s2', 'r', { ops });
    expect(apiFetch.mock.calls[0][1]?.body).toEqual({ ops });
  });

  it('revalidate：POST 空体', async () => {
    await revalidateFeatureEditConstraints('s1', 'ref:a');
    const [path, init] = apiFetch.mock.calls[0];
    expect(path).toBe('/api/v1/chat/sessions/s1/feature-edits/ref%3Aa/revalidate');
    expect(init?.method).toBe('POST');
    expect(init?.body).toEqual({});
  });
});
