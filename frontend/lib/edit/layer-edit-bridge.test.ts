/**
 * layer-edit-bridge 测试（G14 / V04 / V05 / V17 的前端面）：
 * - 手势结束 = 恰好一次 preview + 一次 approve（两次 fetch），无第三写；
 * - 验证失败 / 冲突 / 过期的诚实结果（不重试、不本地伪造成功）；
 * - 退役身份从 selection 语义移除。
 * fetch 打桩（与 lib/feature-edit.test.ts 同款纪律）。
 */
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';

import { submitEditOps, pruneSelectionIds, featureIdFromProperties } from '@/lib/edit/layer-edit-bridge';
import { resetSelectionStore, publishSelection, getSelection } from '@/lib/selection/selection-store';
import { resetUndoForTests } from '@/lib/workbench/undo';

interface FetchCall { url: string; init?: RequestInit }

let calls: FetchCall[] = [];

function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

const fakeFetch = vi.fn(async (url: string, init?: RequestInit) => {
  calls.push({ url, init });
  if (url.endsWith('/feature-edits/preview')) {
    return jsonResponse({
      plan_id: 'fep-test-1',
      committable: true,
      safe: false,
      validation: [],
      preview: [{ wfid: 'wf1-new', presence: 'created' }],
      expected_content_revision: 4,
    });
  }
  if (url.includes('/plans/fep-test-1/decision')) {
    return jsonResponse({
      status: 'applied',
      receipt: {
        entry_seq: 9,
        content_revision_after: 5,
        changed_wfids: ['wf1-new'],
        created_wfids: ['wf1-new'],
        retired_wfids: [],
      },
    });
  }
  return jsonResponse({ ok: true }, 200);
});

describe('submitEditOps', () => {
  beforeEach(() => {
    calls = [];
    vi.stubGlobal('fetch', fakeFetch);
    fakeFetch.mockClear();
    resetUndoForTests();
    resetSelectionStore();
    vi.spyOn(console, 'error').mockImplementation(() => {});
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('一次手势 = 一次 preview + 一次 approve（V04：无多余写）', async () => {
    const result = await submitEditOps('sess-1', 'ref:data-1', [
      { op: 'create_features', features: [{ geometry: { type: 'Point', coordinates: [116, 39] } }] },
    ], { featureIds: undefined, registerUndo: false });
    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.createdWfids).toEqual(['wf1-new']);
    // 恰好两次 REST：preview + decision；手势期间 0 次
    expect(calls.filter((c) => c.url.includes('feature-edits'))).toHaveLength(2);
    expect(calls[0].url).toContain('/feature-edits/preview');
    expect(calls[1].url).toContain('/plans/fep-test-1/decision');
    const approveBody = JSON.parse(String(calls[1].init?.body));
    expect(approveBody).toEqual({ action: 'approve' });
  });

  it('验证失败 → ok:false + findings（不发起 approve）', async () => {
    const rejecting = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.endsWith('/feature-edits/preview')) {
        return jsonResponse({
          plan_id: 'fep-bad',
          committable: false,
          validation: [{ code: 'topology_violation', message: 'features overlap' }],
        });
      }
      return jsonResponse({});
    });
    vi.stubGlobal('fetch', rejecting);
    const result = await submitEditOps('sess-1', 'ref:data-1', [
      { op: 'create_features', features: [{ geometry: { type: 'Point', coordinates: [0, 0] } }] },
    ], {});
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.stage).toBe('preview');
    expect(result.findings?.[0]?.code).toBe('topology_violation');
    // 只有 preview 一次调用
    expect(calls.filter((c) => c.url.includes('decision'))).toHaveLength(0);
  });

  it('提交冲突（409）→ stale_revision（不本地伪造成功）', async () => {
    const conflicting = vi.fn(async (url: string, init?: RequestInit) => {
      calls.push({ url, init });
      if (url.endsWith('/feature-edits/preview')) {
        return jsonResponse({ plan_id: 'fep-x', committable: true, validation: [] });
      }
      return jsonResponse({ detail: { code: 'stale_revision' } }, 409);
    });
    vi.stubGlobal('fetch', conflicting);
    const result = await submitEditOps('sess-1', 'ref:data-1', [{ op: 'delete_features' }], { featureIds: ['wf1-1'] });
    expect(result.ok).toBe(false);
    if (result.ok) return;
    expect(result.code).toBe('stale_revision');
  });

  it('撤销注册走 REST undo/redo（featureEditCommand 语义）', async () => {
    await submitEditOps('sess-1', 'ref:data-1', [
      { op: 'create_features', features: [{ geometry: { type: 'Point', coordinates: [116, 39] } }] },
    ], { registerUndo: true });
    // undo 面板栈有记录
    const { getNextUndoRedoLabels } = await import('@/lib/workbench/undo');
    const labels = getNextUndoRedoLabels();
    expect(labels?.undo).toBeTruthy();
  });
});

describe('pruneSelectionIds（V17）', () => {
  beforeEach(() => resetSelectionStore());
  afterEach(() => resetSelectionStore());

  it('退役身份从 selection 移除；全退役清空', () => {
    publishSelection('select', {
      source: 'map',
      layer_id: 'l1',
      feature_id: 'wf1-a',
      selected_ids: ['wf1-a', 'wf1-b', 'wf1-c'],
    });
    pruneSelectionIds(['wf1-b']);
    expect(getSelection()?.selected_ids).toEqual(['wf1-a', 'wf1-c']);
    pruneSelectionIds(['wf1-a', 'wf1-c']);
    expect(getSelection()?.selected_ids ?? []).toHaveLength(0);
  });

  it('无关身份不动 selection', () => {
    publishSelection('select', {
      source: 'map',
      layer_id: 'l1',
      feature_id: 'wf1-a',
      selected_ids: ['wf1-a'],
    });
    pruneSelectionIds(['wf1-zz']);
    expect(getSelection()?.selected_ids).toEqual(['wf1-a']);
  });
});

describe('featureIdFromProperties', () => {
  it('_wfid 优先；回退 id 链', () => {
    expect(featureIdFromProperties({ _wfid: 'wf1-1', id: 'x' })).toBe('wf1-1');
    expect(featureIdFromProperties({ id: 'sch-1' })).toBe('sch-1');
    expect(featureIdFromProperties({ OBJECTID: 42 })).toBe('42');
    expect(featureIdFromProperties({})).toBeNull();
    expect(featureIdFromProperties(null)).toBeNull();
  });
});
