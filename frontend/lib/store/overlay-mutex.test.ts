/**
 * UI V3 overlay 互斥（真实 store，不走 mock）：
 *
 *   - history / settings / templates 三个 drawer 同时最多打开一个；
 *   - setActiveLeftTab 切 tab 即打开 context panel（nav rail 语义）。
 */
import { beforeEach, describe, expect, it } from 'vitest';
import { useHudStore } from './useHudStore';

beforeEach(() => {
  const s = useHudStore.getState();
  s.setHistoryOpen(false);
  s.setSettingsOpen(false);
  s.setTemplatesOpen(false);
});

describe('overlay mutual exclusion (UI V3)', () => {
  it('opening history closes settings and templates', () => {
    const s = useHudStore.getState();
    s.setSettingsOpen(true);
    s.setHistoryOpen(true);
    const state = useHudStore.getState();
    expect(state.historyOpen).toBe(true);
    expect(state.settingsOpen).toBe(false);
    expect(state.templatesOpen).toBe(false);
  });

  it('opening settings closes history and templates', () => {
    const s = useHudStore.getState();
    s.setHistoryOpen(true);
    s.setSettingsOpen(true);
    const state = useHudStore.getState();
    expect(state.settingsOpen).toBe(true);
    expect(state.historyOpen).toBe(false);
    expect(state.templatesOpen).toBe(false);
  });

  it('opening templates closes history and settings', () => {
    const s = useHudStore.getState();
    s.setSettingsOpen(true);
    s.setTemplatesOpen(true);
    const state = useHudStore.getState();
    expect(state.templatesOpen).toBe(true);
    expect(state.settingsOpen).toBe(false);
    expect(state.historyOpen).toBe(false);
  });

  it('closing a drawer does not touch the others', () => {
    const s = useHudStore.getState();
    s.setHistoryOpen(true);
    s.setHistoryOpen(false);
    expect(useHudStore.getState().settingsOpen).toBe(false);
    expect(useHudStore.getState().templatesOpen).toBe(false);
  });
});

describe('nav rail tab activation (UI V3)', () => {
  it('setActiveLeftTab opens the context panel', () => {
    const s = useHudStore.getState();
    if (s.leftPanelOpen) s.toggleLeftPanel(); // 确保从折叠开始
    expect(useHudStore.getState().leftPanelOpen).toBe(false);

    s.setActiveLeftTab('layers');
    const state = useHudStore.getState();
    expect(state.activeLeftTab).toBe('layers');
    expect(state.leftPanelOpen).toBe(true);

    // 还原默认状态，避免影响其它测试文件共享的 store
    s.setActiveLeftTab('chat');
  });
});
