import { render, screen, fireEvent } from '@testing-library/react';
import ExportLayoutTab from './export-layout-tab';
import { useHudStore } from '@/lib/store/useHudStore';
import { beforeEach, describe, expect, it } from 'vitest';

describe('ExportLayoutTab', () => {
  beforeEach(() => {
    useHudStore.setState({
      exportSettings: {
        isExportMode: false,
        title: '',
        subtitle: '',
        showWatermark: true,
        showCompass: true,
        showScale: true,
        showLegend: true,
        paperSize: 'screen',
        orientation: 'landscape',
        dpi: 96,
        format: 'png',
      },
    });
  });

  it('enables export mode on mount and disables on unmount', () => {
    expect(useHudStore.getState().exportSettings.isExportMode).toBe(false);

    const { unmount } = render(<ExportLayoutTab />);
    
    expect(useHudStore.getState().exportSettings.isExportMode).toBe(true);

    unmount();
    
    expect(useHudStore.getState().exportSettings.isExportMode).toBe(false);
  });

  it('updates title in store when changed', () => {
    render(<ExportLayoutTab />);
    
    const titleInput = screen.getByPlaceholderText('如：成都市高校分布图');
    fireEvent.change(titleInput, { target: { value: 'New Title' } });
    
    expect(useHudStore.getState().exportSettings.title).toBe('New Title');
  });

  it('updates format in store when changed', () => {
    render(<ExportLayoutTab />);
    
    const formatSelect = screen.getByDisplayValue('PNG 图片');
    fireEvent.change(formatSelect, { target: { value: 'pdf' } });
    
    expect(useHudStore.getState().exportSettings.format).toBe('pdf');
  });

  it('updates checkbox state when clicked', () => {
    render(<ExportLayoutTab />);
    
    const compassCheckbox = screen.getByLabelText('指北针');
    expect(compassCheckbox).toBeChecked();
    
    fireEvent.click(compassCheckbox);
    expect(useHudStore.getState().exportSettings.showCompass).toBe(false);
    expect(compassCheckbox).not.toBeChecked();
  });
});
