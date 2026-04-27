import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { UploadZone } from './upload-zone';

vi.mock('@/lib/api/upload', () => ({
  uploadFile: vi.fn(),
}));

import { uploadFile } from '@/lib/api/upload';

describe('UploadZone', () => {
  const onUploadSuccess = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders upload area with instructions', () => {
    render(<UploadZone onUploadSuccess={onUploadSuccess} />);
    expect(screen.getByText('拖放或点击上传 GIS 数据')).toBeInTheDocument();
    expect(screen.getByText(/GeoJSON \/ Shapefile \/ KML/)).toBeInTheDocument();
  });

  it('renders compact mode without text', () => {
    render(<UploadZone onUploadSuccess={onUploadSuccess} compact />);
    expect(screen.queryByText('拖放或点击上传 GIS 数据')).not.toBeInTheDocument();
    // Should have a file input
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    expect(input).toBeInTheDocument();
  });

  it('calls uploadFile on file selection', async () => {
    vi.mocked(uploadFile).mockResolvedValueOnce({ id: 1, filename: 'test.geojson' } as any);
    render(<UploadZone onUploadSuccess={onUploadSuccess} sessionId="s1" />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['{}'], 'test.geojson', { type: 'application/geo+json' });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => {
      expect(uploadFile).toHaveBeenCalledWith(file, 's1', expect.any(Function));
    });
  });

  it('calls onUploadSuccess after successful upload', async () => {
    const result = { id: 1, filename: 'test.geojson' };
    vi.mocked(uploadFile).mockImplementationOnce((_file, _sid, onProgress) => {
      onProgress?.(100);
      return Promise.resolve(result as any);
    });
    render(<UploadZone onUploadSuccess={onUploadSuccess} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['{}'], 'test.geojson', { type: 'application/geo+json' });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => {
      expect(onUploadSuccess).toHaveBeenCalledWith(result);
    });
  });

  it('shows error when upload fails', async () => {
    vi.mocked(uploadFile).mockRejectedValueOnce(new Error('File too large'));
    render(<UploadZone onUploadSuccess={onUploadSuccess} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    const file = new File(['{}'], 'test.geojson', { type: 'application/geo+json' });
    fireEvent.change(input, { target: { files: [file] } });
    await waitFor(() => {
      expect(screen.getByText('File too large')).toBeInTheDocument();
    });
  });

  it('dismisses error when close button clicked', async () => {
    vi.mocked(uploadFile).mockRejectedValueOnce(new Error('Error'));
    render(<UploadZone onUploadSuccess={onUploadSuccess} />);
    const input = document.querySelector('input[type="file"]') as HTMLInputElement;
    fireEvent.change(input, { target: { files: [new File(['{}'], 't.geojson')] } });
    await waitFor(() => {
      expect(screen.getByText('Error')).toBeInTheDocument();
    });
    // Click the dismiss button (second X button in error row)
    const buttons = screen.getAllByRole('button');
    fireEvent.click(buttons[buttons.length - 1]);
    expect(screen.queryByText('Error')).not.toBeInTheDocument();
  });
});
