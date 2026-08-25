import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useHudStore } from '@/lib/store/useHudStore';
import { getPendingPresentation } from '@/lib/mapspec/session-cursor';
import {
  currentTurn,
  nextTurn,
  noteAgentDisplayed,
  resetTurnFocusForTests,
  tagUserDisplayed,
} from './turn-focus';

/** 「地图随对话」契约：Agent 在新轮展示图层时，旧轮的可见分析图层收起
 * （HUD visible + pending presentation）；用户手动点开的层不与对抗。 */

vi.mock('@/lib/utils/logger', () => ({
  devOnly: { warn: vi.fn(), info: vi.fn(), error: vi.fn() },
}));

function addAnalysisLayer(id: string, visible: boolean, specLayerId?: string) {
  useHudStore.getState().addLayer({
    id,
    name: id,
    type: 'vector',
    visible,
    opacity: 1,
    group: 'analysis',
    source: { type: 'FeatureCollection', features: [] } as any,
    ...(specLayerId ? { _mapspecLayerId: specLayerId } : {}),
  } as any);
}

beforeEach(() => {
  resetTurnFocusForTests();
  useHudStore.getState().clearLayers();
});

describe('turn-focus', () => {
  it('新轮 agent 展示 → 旧轮可见分析层收起(含 pending presentation),同轮层保留', () => {
    addAnalysisLayer('L-old', true, 'spec-old');
    addAnalysisLayer('L-same-turn', true);
    noteAgentDisplayed('L-old');
    noteAgentDisplayed('L-same-turn');
    addAnalysisLayer('L-base', true, undefined);
    useHudStore.getState().updateLayer('L-base', { group: 'base' } as any);

    nextTurn(); // 用户发送新一轮消息
    addAnalysisLayer('L-new', true, 'spec-new');
    noteAgentDisplayed('L-new');

    const layers = useHudStore.getState().layers;
    const byId = Object.fromEntries(layers.map((l) => [l.id, l]));
    expect(byId['L-new'].visible).toBe(true);
    expect(byId['L-new']._displayTurn).toBe(currentTurn());
    // 旧轮层收起 + committed spec 层同步 pending presentation(#737 机制)
    expect(byId['L-old'].visible).toBe(false);
    expect(getPendingPresentation()['spec-old']).toEqual({ visible: false });
    // 同轮标记过的层不受收起影响
    expect(byId['L-same-turn'].visible).toBe(false); // 旧轮标记 → 收起 ✓
    // 非 analysis 组不动
    expect(byId['L-base'].visible).toBe(true);
  });

  it('同轮内 agent 多次展示互不收起', () => {
    nextTurn();
    addAnalysisLayer('A', true);
    noteAgentDisplayed('A');
    addAnalysisLayer('B', true);
    noteAgentDisplayed('B');
    const byId = Object.fromEntries(useHudStore.getState().layers.map((l) => [l.id, l]));
    expect(byId.A.visible).toBe(true);
    expect(byId.B.visible).toBe(true);
  });

  it('用户手动点开的层标记当前轮,本轮后续 agent 展示不收起', () => {
    nextTurn();
    addAnalysisLayer('user-shown', false);
    tagUserDisplayed('user-shown');
    addAnalysisLayer('agent-shown', true);
    noteAgentDisplayed('agent-shown');
    const byId = Object.fromEntries(useHudStore.getState().layers.map((l) => [l.id, l]));
    expect(byId['user-shown'].visible).toBe(true);
    expect(byId['agent-shown'].visible).toBe(true);
  });

  it('未标记的会话恢复层(无 _displayTurn)在新轮展示时让位', () => {
    addAnalysisLayer('restored', true);
    nextTurn();
    addAnalysisLayer('fresh', true);
    noteAgentDisplayed('fresh');
    const byId = Object.fromEntries(useHudStore.getState().layers.map((l) => [l.id, l]));
    expect(byId.restored.visible).toBe(false);
    expect(byId.fresh.visible).toBe(true);
  });

  it('已隐藏的旧轮层不受影响(sweep 只动可见层)', () => {
    nextTurn();
    addAnalysisLayer('hidden-old', false);
    noteAgentDisplayed('hidden-old'); // 标记但 hidden
    nextTurn();
    addAnalysisLayer('new', true);
    noteAgentDisplayed('new');
    const byId = Object.fromEntries(useHudStore.getState().layers.map((l) => [l.id, l]));
    expect(byId['hidden-old'].visible).toBe(false);
  });
});
