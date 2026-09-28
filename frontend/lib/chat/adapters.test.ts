/**
 * typed adapters 决策表测试（H03 / #1554）。
 *
 * adapters 是 `(payload, ctx) => Decision` 纯函数；本文件钉死每个 adapter
 * 的决策边界 —— 特别是跨会话守卫、幂等门、阈值分流与错误分类透传。
 */
import { describe, it, expect } from 'vitest';
import {
  errorEventDecision,
  mapFinalizationDecision,
  planReadyDecision,
  stepResultDecision,
  tokenDecision,
  VECTOR_TILE_THRESHOLD,
} from './adapters';

const NOW = 1_700_000_000_000;
const baseCtx = {
  sessionId: 's-1',
  accentColor: '#123456',
  now: NOW,
  labelFor: (tool: string, name?: string) => `label:${tool}:${name ?? ''}`,
};

describe('tokenDecision', () => {
  it('routes token and content, rejects structural events', () => {
    expect(tokenDecision({ event: 'token', data: { content: 'a', is_reasoning: true } }))
      .toEqual({ chunk: 'a', isReasoning: true });
    expect(tokenDecision({ event: 'content', data: { content: '' } }))
      .toEqual({ chunk: '', isReasoning: false });
    // reasoning type 别名（Pi content 事件带 type: reasoning）
    expect(tokenDecision({ event: 'content', data: { content: 'x', type: 'reasoning' } }))
      .toEqual({ chunk: 'x', isReasoning: true });
    expect(tokenDecision({ event: 'step_result', data: { content: 'x' } })).toBeNull();
    expect(tokenDecision({ event: 'done', data: {} })).toBeNull();
  });
});

describe('planReadyDecision', () => {
  it('maps done→status and defaults domains/tool_family', () => {
    const d = planReadyDecision({
      intent: '分析',
      steps: [
        { n: 1, goal: 'g1', tool_family: 'gis', done: true },
        { n: 2, goal: 'g2' },
      ],
    })!;
    expect(d.intent).toBe('分析');
    expect(d.domains).toEqual([]);
    expect(d.steps[0]).toMatchObject({ n: 1, status: 'done', tool_family: 'gis' });
    expect(d.steps[1]).toMatchObject({ n: 2, status: 'pending', tool_family: 'core' });
    expect(d.finalized).toBe(false);
  });

  it('survives malformed payloads (null decision, no throw)', () => {
    expect(planReadyDecision(null as never)).toBeNull();
    // steps 非数组 → map 抛出 → 捕获为 null（不炸事件链）
    expect(planReadyDecision({ steps: 'not-a-list', intent: 1 } as never)).toBeNull();
  });
});

describe('mapFinalizationDecision', () => {
  const noticeOf = (p: Record<string, unknown>) =>
    p.status === 'needs_repair' ? `需要修复: ${p.status}` : null;

  it('drops cross-session events (INV-2)', () => {
    const d = mapFinalizationDecision(
      { session_id: 'other', status: 'needs_repair', result_bbox: [1, 2, 3, 4] },
      { sessionId: 's-1', lastNotice: null },
      noticeOf,
    );
    expect(d).toEqual({ command: null, notice: null, nextLastNotice: null });
  });

  it('bbox-gated command; no bbox → no command (review D-6 idempotence)', () => {
    const d1 = mapFinalizationDecision(
      { session_id: 's-1', status: 'complete', result_bbox: [1, 2, 3, 4] },
      { sessionId: 's-1', lastNotice: null },
      noticeOf,
    );
    expect(d1.command).toEqual({
      command: 'MAP_FINALIZATION',
      params: { status: 'complete', bbox: [1, 2, 3, 4] },
    });
    const d2 = mapFinalizationDecision(
      { session_id: 's-1', status: 'complete' },
      { sessionId: 's-1', lastNotice: null },
      noticeOf,
    );
    expect(d2.command).toBeNull();
  });

  it('notice dedupe: same state+session only once; status change re-notifies (review H-6)', () => {
    const payload = { session_id: 's-1', status: 'needs_repair', result_bbox: [1, 2, 3, 4] };
    const first = mapFinalizationDecision(payload, { sessionId: 's-1', lastNotice: null }, noticeOf);
    expect(first.notice?.text).toContain('需要修复');
    // 同态重复 → 静默，去重状态原样透传（不变）
    const dup = mapFinalizationDecision(
      payload,
      { sessionId: 's-1', lastNotice: first.nextLastNotice },
      noticeOf,
    );
    expect(dup.notice).toBeNull();
    expect(dup.nextLastNotice).toBe(first.nextLastNotice);
    // 状态变化 → 重新评估披露（failed 不在 noticeOf 词表 → 无新披露，
    // 去重状态保持不变）
    const changed = mapFinalizationDecision(
      { ...payload, status: 'failed' },
      { sessionId: 's-1', lastNotice: first.nextLastNotice },
      noticeOf,
    );
    expect(changed.notice).toBeNull();
    expect(changed.nextLastNotice).toBe(first.nextLastNotice);
  });
});

describe('errorEventDecision', () => {
  it('step_error: tool status with stepId, no stream-level finalize', () => {
    const d = errorEventDecision({
      event: 'step_error',
      data: { tool: 'buffer', error: '几何无效', step_id: 's3', error_class: 'tool_error' },
    });
    expect(d).toMatchObject({
      toolStatus: { tool: 'buffer', error: '几何无效', stepId: 's3' },
      resetPendingToolArgs: false,
      finalizeAllRunning: false,
      detail: '几何无效',
    });
  });

  it('stream-level error: resets pending args + finalizes all running', () => {
    for (const name of ['error', 'task_error'] as const) {
      const d = errorEventDecision({ event: name, data: {} });
      expect(d.resetPendingToolArgs).toBe(true);
      expect(d.finalizeAllRunning).toBe(true);
      expect(d.toolStatus).toBeNull();
      expect(d.detail).toBe('请求失败，请重试。');
    }
  });

  it('detail fallbacks per event kind (B-P2-13 keeps real detail when present)', () => {
    expect(errorEventDecision({ event: 'step_error', data: { tool: 't' } }).detail).toBe('工具执行失败。');
    expect(errorEventDecision({ event: 'task_error', data: { error: '预算耗尽' } }).detail).toBe('预算耗尽');
  });
});

describe('stepResultDecision', () => {
  it('workbench capture + tool status with hasGeojson extra', () => {
    const d = stepResultDecision({
      tool: 'buffer',
      step_id: 's1',
      geojson_ref: 'ref:g1',
      ref_descriptor: { ref_id: 'ref:g1', mvt_capable: false, feature_count: 10 },
      result: { success: true, legend_spec: { stops: [] } },
    } as never, baseCtx);
    expect(d.workbenchCapture).toBeDefined();
    expect(d.toolStatus?.tool).toBe('buffer');
    expect(d.toolStatus?.stepId).toBe('s1');
    expect(d.toolStatus?.extra).toMatchObject({ hasGeojson: true, layerId: 'ref:g1' });
  });

  it('propose_plan → planProposal with defaults', () => {
    const d = stepResultDecision({
      tool: 'propose_plan',
      result: { success: true, plan_id: 'p1', title: 'T' },
    } as never, baseCtx);
    expect(d.planProposal).toMatchObject({
      plan_id: 'p1', title: 'T', step_count: 0,
      destructive_steps: [], steps_preview: [], status: 'pending',
    });
    expect(d.layerMount).toBeNull();
  });

  it('explorer_task passthrough (#518)', () => {
    const d = stepResultDecision({
      tool: 'deep_explore',
      result: { type: 'explorer_task', task_id: 'ex-9' },
    } as never, baseCtx);
    expect(d.explorerTaskId).toBe('ex-9');
  });

  it('layer mount: label via injected i18n, mvt tile url, runtime patch fields', () => {
    const d = stepResultDecision({
      tool: 'search_poi',
      name: '小学',
      geojson_ref: 'ref:poi',
      ref_descriptor: { ref_id: 'ref:poi', mvt_capable: true, feature_count: 10 },
      result: {
        success: true,
        runtime_patch: { visible: true, opacity: 0.7, layer_id: 'l1', mapspec_fingerprint: 'mf' },
        layer_meta: { title: 'POI 学校' },
      },
    } as never, baseCtx);
    const mount = d.layerMount!;
    expect(mount.layerId).toBe('ref:poi');
    expect(mount.addLayerArg).toMatchObject({
      name: 'label:search_poi:小学',
      type: 'vector',
      visible: true,
      opacity: 0.7,
      _refId: 'ref:poi',
      _mapspecLayerId: 'l1',
      _mapspecFingerprint: 'mf',
    });
    expect((mount.addLayerArg as Record<string, unknown>)._tileUrl).toContain('ref:poi');
    expect(mount.updateLayerArg).toMatchObject({ id: 'ref:poi' });
    expect(mount.agentDisplayed).toBe(true);
    expect(mount.cartographyTitle).toBe('POI 学校');
    expect(mount.shouldFetchFullFC).toBe(true); // 10 ≤ 阈值 → 内联 GeoJSON
    expect(d.chip?.layerName).toBe('label:search_poi:小学');
  });

  it('fetch decision: large tile-capable layers skip full FC; small/undescriptor fetch', () => {
    const mk = (descriptor: Record<string, unknown> | undefined) =>
      stepResultDecision({
        tool: 'buffer', geojson_ref: 'ref:x', ref_descriptor: descriptor,
        result: { success: true },
      } as never, baseCtx).layerMount!.shouldFetchFullFC;
    expect(mk({ mvt_capable: true, feature_count: VECTOR_TILE_THRESHOLD + 1 })).toBe(false);
    expect(mk({ mvt_capable: true, feature_count: VECTOR_TILE_THRESHOLD })).toBe(true);
    expect(mk({ mvt_capable: false, feature_count: 1_000_000 })).toBe(true);
    expect(mk(undefined)).toBe(true);
  });

  it('image result mounts as heatmap; chart passthrough', () => {
    const d = stepResultDecision({
      tool: 'heatmap_data',
      result: { image: 'blob:x', chart: { series: [] } },
    } as never, baseCtx);
    expect(d.layerMount!.addLayerArg).toMatchObject({ type: 'heatmap' });
    expect(d.chart).toEqual({ series: [] });
  });
});
