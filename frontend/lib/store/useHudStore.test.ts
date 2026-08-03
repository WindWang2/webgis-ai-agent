import { describe, it, expect } from 'vitest';
import { useHudStore, EmbodiedHudEngine } from './useHudStore';

describe('useHudStore - ExportSettings', () => {
  it('should update exportSettings', () => {
    const store = useHudStore.getState();
    expect(store.exportSettings.isExportMode).toBe(false);

    store.updateExportSettings({ isExportMode: true, title: 'Test Title' });
    
    const updatedStore = useHudStore.getState();
    expect(updatedStore.exportSettings.isExportMode).toBe(true);
    expect(updatedStore.exportSettings.title).toBe('Test Title');
    expect(updatedStore.exportSettings.format).toBe('png');
  });
});

describe('EmbodiedHudEngine', () => {
  it('should toggle left drawer and record snapshots for undo/redo', () => {
    EmbodiedHudEngine.resetState();
    expect(useHudStore.getState().hudOpen).toBe(false);

    EmbodiedHudEngine.toggleLeftDrawer('layers');
    expect(useHudStore.getState().hudOpen).toBe(true);
    expect(useHudStore.getState().activeLeftTab).toBe('layers');

    EmbodiedHudEngine.setActiveTool('buffer');
    expect(useHudStore.getState().activeTool).toBe('buffer');

    const undone = EmbodiedHudEngine.undo();
    expect(undone).toBe(true);
  });
});
