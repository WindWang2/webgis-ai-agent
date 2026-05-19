/**
 * Slice 拆分回归测试 (M3)：
 *
 * 切完后逐 slice 验证：
 *   - 每个 slice 自身的 action 走通
 *   - 多个 slice 的 action 在同一 store 内不串台
 *   - DEMO 常量从 useHudStore 仍可 re-export（向后兼容）
 */
import { beforeEach, describe, expect, it } from 'vitest';
import {
  useHudStore,
  DEMO_LAYERS,
  DEMO_MESSAGES,
  DEMO_OPS_LOG,
} from './useHudStore';

beforeEach(() => {
  const s = useHudStore.getState();
  // 每个 case 跑前把会受影响的字段重置
  s.clearLayers();
  s.clearTask();
  s.clearProcessLayers();
  s.clearOpsLog();
  s.clearCausalChain();
});


describe('layers slice', () => {
  it('addLayer/removeLayer/toggle/update 都生效', () => {
    const s = useHudStore.getState();
    s.addLayer({ id: 'L1', name: 'A', type: 'vector', visible: true, opacity: 1, group: 'analysis', source: { type: 'FeatureCollection', features: [] } as any });
    expect(useHudStore.getState().layers).toHaveLength(1);
    s.toggleLayer('L1');
    expect(useHudStore.getState().layers[0].visible).toBe(false);
    s.updateLayer('L1', { opacity: 0.5 });
    expect(useHudStore.getState().layers[0].opacity).toBe(0.5);
    s.removeLayer('L1');
    expect(useHudStore.getState().layers).toHaveLength(0);
  });

  it('process layers 不影响主 layers', () => {
    const s = useHudStore.getState();
    s.addProcessLayer('step-1', { type: 'FeatureCollection', features: [] } as any);
    expect(useHudStore.getState().processLayers['step-1']).toBeDefined();
    expect(useHudStore.getState().layers).toHaveLength(0);
    s.removeProcessLayer('step-1');
    expect(useHudStore.getState().processLayers['step-1']).toBeUndefined();
  });
});


describe('task slice', () => {
  it('task lifecycle 完整推进', () => {
    const s = useHudStore.getState();
    s.taskStart('T1');
    s.stepStart('T1', 's1', 0, 'buffer_analysis');
    expect(useHudStore.getState().currentTask?.steps).toHaveLength(1);
    s.stepResult('T1', 's1', 'buffer_analysis', { ok: true }, false);
    expect(useHudStore.getState().currentTask?.steps[0].status).toBe('completed');
    s.taskComplete('T1', 1, 'done');
    expect(useHudStore.getState().currentTask?.status).toBe('completed');
  });

  it('错误 taskId 不污染当前任务', () => {
    const s = useHudStore.getState();
    s.taskStart('T1');
    s.stepStart('T_WRONG', 's1', 0, 'x');
    expect(useHudStore.getState().currentTask?.steps).toHaveLength(0);
  });

  it('explorer task 与 chat task 隔离', () => {
    const s = useHudStore.getState();
    s.addExplorerTask({ taskId: 'EX1', status: 'planning' as any, stage: 'plan' as any, progress: 0 } as any);
    s.taskStart('T1');
    expect(useHudStore.getState().explorerTasks).toHaveLength(1);
    expect(useHudStore.getState().currentTask?.id).toBe('T1');
  });
});


describe('settings slice', () => {
  it('skill toggle 翻转 enabled', () => {
    const s = useHudStore.getState();
    const first = useHudStore.getState().skills[0];
    s.toggleSkill(first.id);
    const next = useHudStore.getState().skills.find((sk) => sk.id === first.id)!;
    expect(next.enabled).toBe(!first.enabled);
  });

  it('llmConfigFull 合并不丢字段', () => {
    const s = useHudStore.getState();
    s.setLlmConfigFull({ model: 'gpt-4o-mini' });
    expect(useHudStore.getState().llmConfigFull.model).toBe('gpt-4o-mini');
    // baseUrl / caching 等其它字段被保留
    expect(useHudStore.getState().llmConfigFull.baseUrl).toBeDefined();
  });

  it('ragConfig 部分更新合并', () => {
    const s = useHudStore.getState();
    s.setRagConfig({ topK: 9 });
    expect(useHudStore.getState().ragConfig.topK).toBe(9);
    expect(useHudStore.getState().ragConfig.spatialWeight).toBe(60); // 未传仍是默认
  });
});


describe('ui slice', () => {
  it('viewport set 完整 round-trip', () => {
    const s = useHudStore.getState();
    s.setViewport([120, 30], 11, 0, 45);
    expect(useHudStore.getState().viewport.zoom).toBe(11);
    expect(useHudStore.getState().viewport.pitch).toBe(45);
  });

  it('perception 队列 push + drain 是 FIFO', () => {
    const s = useHudStore.getState();
    s.pushPerception('a', { i: 1 });
    s.pushPerception('b', { i: 2 });
    const drained = s.drainPerception();
    expect(drained.map((e) => e.event)).toEqual(['a', 'b']);
    expect(useHudStore.getState()._perceptionQueue).toHaveLength(0);
  });

  it('opsLog 新条目在前', () => {
    const s = useHudStore.getState();
    s.pushOpLog({ id: '1', type: 'add', label: 'L1', time: 't', detail: 'd' });
    s.pushOpLog({ id: '2', type: 'add', label: 'L2', time: 't', detail: 'd' });
    expect(useHudStore.getState().opsLog[0].id).toBe('2');
  });
});


describe('DEMO re-exports', () => {
  it('demo 常量从 useHudStore 仍可访问', () => {
    expect(DEMO_LAYERS.length).toBeGreaterThan(0);
    expect(DEMO_MESSAGES.length).toBeGreaterThan(0);
    expect(DEMO_OPS_LOG.length).toBeGreaterThan(0);
  });
});
