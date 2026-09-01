import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { PreviewModal } from '@/components/explorer/preview-modal';
import type { GeoJSONFeatureCollection } from '@/lib/types';

describe('Spatial Explorer PreviewModal Integration Tests', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  const sampleGeoJSON: GeoJSONFeatureCollection = {
    type: 'FeatureCollection',
    features: [
      {
        type: 'Feature',
        id: 'geo-1',
        geometry: { type: 'Point', coordinates: [104.0668, 30.5728] },
        properties: {
          name: '天府国际金融中心',
          zone: '高新区',
          towers: 9,
        },
      },
      {
        type: 'Feature',
        id: 'geo-2',
        geometry: { type: 'Point', coordinates: [104.0633, 30.5689] },
        properties: {
          name: '环球中心',
          zone: '高新区',
          towers: 1,
        },
      },
    ],
  };

  it('renders complete preview modal with GeoJSON FeatureCollection and enables switching tabs', async () => {
    const user = userEvent.setup();
    const handleClose = vi.fn();
    render(<PreviewModal result={sampleGeoJSON} title="成都地标 POI" onClose={handleClose} />);

    // Check title and feature count
    expect(screen.getByText('成都地标 POI')).toBeInTheDocument();
    expect(screen.getByText('共 2 要素')).toBeInTheDocument();

    // Default tab shows table with features
    expect(screen.getByText('天府国际金融中心')).toBeInTheDocument();
    expect(screen.getByText('环球中心')).toBeInTheDocument();

    // Switch to Raw JSON tab
    const jsonTab = screen.getByRole('tab', { name: '原始 JSON' });
    await user.click(jsonTab);

    expect(screen.getByText(/天府国际金融中心/)).toBeInTheDocument();

    // Copy JSON action
    const writeTextSpy = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      value: { writeText: writeTextSpy },
      writable: true,
      configurable: true,
    });

    const copyBtn = screen.getByRole('button', { name: '复制原始 JSON 数据' });
    fireEvent.click(copyBtn);

    expect(writeTextSpy).toHaveBeenCalledTimes(1);
    expect(await screen.findByText('已复制')).toBeInTheDocument();
  });
});
