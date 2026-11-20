import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { installInMemoryLocalStorage } from '@/test/in-memory-local-storage';
import {
  PERSIST_KEY,
  disableHudPersistWrites,
  enableHudPersistWrites,
  useHudStore,
  rehydrateHudStore,
} from './useHudStore';

describe('hud persist hydration gate', () => {
  beforeEach(() => {
    installInMemoryLocalStorage();
    enableHudPersistWrites();
    useHudStore.setState({ baseLayer: 'Carto 深色' });
  });

  afterEach(() => vi.restoreAllMocks());

  it('does not let a pre-rehydrate set() clobber the persisted base layer', async () => {
    localStorage.setItem(
      PERSIST_KEY,
      JSON.stringify({ state: { baseLayer: 'ESRI 影像' }, version: 0 }),
    );
    disableHudPersistWrites();

    // First-mount noise (map loaded / ai status) used to persist DEFAULTS
    // and wipe the user's layer before rehydrate ran.
    useHudStore.setState({ aiStatus: 'thinking' });
    expect(JSON.parse(localStorage.getItem(PERSIST_KEY)!).state.baseLayer).toBe(
      'ESRI 影像',
    );

    await useHudStore.persist.rehydrate();
    enableHudPersistWrites();
    expect(useHudStore.getState().baseLayer).toBe('ESRI 影像');
  });
});


describe('viewport default restoration', () => {
  beforeEach(() => {
    installInMemoryLocalStorage();
    disableHudPersistWrites();
    useHudStore.setState({ leftPanelOpen: true });
    vi.spyOn(useHudStore.persist, 'hasHydrated').mockReturnValue(false);
    vi.stubGlobal('innerWidth', 390);
  });
  afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

  it('closes a new mobile workspace after mount', async () => {
    await rehydrateHudStore();
    expect(useHudStore.getState().leftPanelOpen).toBe(false);
  });

  it('keeps a saved panel preference instead of overwriting storage with viewport defaults', async () => {
    localStorage.setItem(PERSIST_KEY, JSON.stringify({ state: { leftPanelOpen: true, baseLayer: 'ESRI 影像' }, version: 0 }));
    await rehydrateHudStore();
    expect(useHudStore.getState().leftPanelOpen).toBe(true);
    expect(useHudStore.getState().baseLayer).toBe('ESRI 影像');
    expect(JSON.parse(localStorage.getItem(PERSIST_KEY)!).state.leftPanelOpen).toBe(true);
  });

  it('does not reset a user choice on provider remount', async () => {
    vi.mocked(useHudStore.persist.hasHydrated).mockReturnValue(true);
    await rehydrateHudStore();
    expect(useHudStore.getState().leftPanelOpen).toBe(true);
  });
});
