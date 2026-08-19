import { describe, it, expect, beforeEach } from 'vitest';
import { installInMemoryLocalStorage } from '@/test/in-memory-local-storage';
import {
  PERSIST_KEY,
  disableHudPersistWrites,
  enableHudPersistWrites,
  useHudStore,
} from './useHudStore';

describe('hud persist hydration gate', () => {
  beforeEach(() => {
    installInMemoryLocalStorage();
    enableHudPersistWrites();
    useHudStore.setState({ baseLayer: 'Carto 深色' });
  });

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
