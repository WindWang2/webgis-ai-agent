import React from 'react';
import { render } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi, Mock, beforeAll, afterAll } from 'vitest';
import '@testing-library/jest-dom';
import { ExportMask } from '@/components/map/export-mask';
import { useHudStore } from '@/lib/store/useHudStore';

// Mock the store
vi.mock('@/lib/store/useHudStore', () => ({
  useHudStore: vi.fn(),
}));

describe('ExportMask', () => {
  beforeAll(() => {
    global.ResizeObserver = class ResizeObserver {
      observe() {}
      unobserve() {}
      disconnect() {}
    };
  });

  afterAll(() => {
    delete (global as any).ResizeObserver;
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders nothing when not in export mode', () => {
    (useHudStore as unknown as Mock).mockImplementation((selector) => {
      const state = {
        exportSettings: {
          isExportMode: false,
          paperSize: 'A4',
          orientation: 'landscape',
          title: '',
          subtitle: '',
          showWatermark: false,
        },
        theme: 'light',
      };
      return selector(state);
    });

    const { container } = render(<ExportMask />);
    expect(container.firstChild).toBeNull();
  });

  it('renders the mask + watermark (no parallel local title) when in export mode', () => {
    (useHudStore as unknown as Mock).mockImplementation((selector) => {
      const state = {
        exportSettings: {
          isExportMode: true,
          paperSize: 'A4',
          orientation: 'landscape',
          title: 'Test Title',
          subtitle: 'Test Subtitle',
          showWatermark: true,
        },
        theme: 'light',
      };
      return selector(state);
    });

    const { getByText, queryByText } = render(<ExportMask />);
    // PR-C：标题是 MapSpec 组件（live chrome 渲染）；蒙层不得再画本地
    // exportSettings 的平行标题（会与 spec 标题重复/陈旧）。
    expect(queryByText('Test Title')).toBeNull();
    expect(queryByText('Test Subtitle')).toBeNull();
    expect(getByText('由 WebGIS AI Agent 生成')).toBeInTheDocument();
  });

  it('keeps watermark visible when paperSize is screen', () => {
    (useHudStore as unknown as Mock).mockImplementation((selector) => {
      const state = {
        exportSettings: {
          isExportMode: true,
          paperSize: 'screen',
          orientation: 'landscape',
          title: 'Screen Title',
          subtitle: 'Screen Subtitle',
          showWatermark: true,
        },
        theme: 'light',
      };
      return selector(state);
    });

    const { getByText, queryByText, container } = render(<ExportMask />);
    expect(queryByText('Screen Title')).toBeNull();
    expect(queryByText('Screen Subtitle')).toBeNull();
    expect(getByText('由 WebGIS AI Agent 生成')).toBeInTheDocument();
    
    // Check if the wrapper div does not have display: none
    const div = container.firstChild as HTMLElement;
    expect(div).not.toHaveStyle({ display: 'none' });
    expect(div).toHaveStyle({ borderWidth: '0px' });
  });
});
