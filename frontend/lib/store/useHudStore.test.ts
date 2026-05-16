import { describe, it, expect } from 'vitest';
import { useHudStore } from './useHudStore';

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
