import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { API_BASE } from '@/lib/api/config';
import MiniMd from './mini-md';

/**
 * #515 regression: chat-embedded export links/images point at protected
 * download routes that only accept `Authorization: Bearer`. A bare `<a>` /
 * `<img>` is a browser-native request → hard 401. MiniMd must intercept
 * protected links (auth blob download) and images (blob → object URL).
 */
const downloadWithAuthMock = vi.fn();
vi.mock('@/lib/api/authenticated-download', () => ({
  isProtectedDownloadUrl: (url: string) =>
    typeof url === 'string' && url.includes('/api/v1/export/download/'),
  downloadWithAuth: (...args: unknown[]) => downloadWithAuthMock(...args),
}));

const apiFetchBlobMock = vi.fn();
vi.mock('@/lib/api/transport', () => ({
  apiFetchBlob: (...args: unknown[]) => apiFetchBlobMock(...args),
}));

const exportUrl = (name: string) => `${API_BASE}/api/v1/export/download/${name}`;

beforeEach(() => {
  vi.clearAllMocks();
  apiFetchBlobMock.mockReset();
  apiFetchBlobMock.mockResolvedValue({ blob: new Blob(['img'], { type: 'image/png' }), filename: null });
});

describe('MiniMd protected download handling (#515)', () => {
  it('intercepts clicks on protected download links and downloads via transport', async () => {
    downloadWithAuthMock.mockResolvedValue(undefined);
    render(<MiniMd text={`[下载SVG](${exportUrl('map_export_1.svg')})`} />);

    const link = screen.getByText('下载SVG').closest('a');
    expect(link).not.toBeNull();

    fireEvent.click(link!);

    await waitFor(() => expect(downloadWithAuthMock).toHaveBeenCalledTimes(1));
    expect(downloadWithAuthMock).toHaveBeenCalledWith(exportUrl('map_export_1.svg'));
  });

  it('does not intercept ordinary external links', () => {
    render(<MiniMd text={`[官网](https://example.com)`} />);
    const link = screen.getByText('官网').closest('a');
    expect(link).not.toBeNull();
    fireEvent.click(link!);
    expect(downloadWithAuthMock).not.toHaveBeenCalled();
  });

  it('renders protected images from an authenticated blob (object URL)', async () => {
    const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:img-1');
    render(<MiniMd text={`![地图](${exportUrl('map_export_2.png')})`} />);

    await waitFor(() => expect(apiFetchBlobMock).toHaveBeenCalledTimes(1));
    expect(apiFetchBlobMock).toHaveBeenCalledWith('/api/v1/export/download/map_export_2.png');

    const img = screen.getByRole('img', { name: '地图' }) as HTMLImageElement;
    expect(img.src).toContain('blob:img-1');
    createSpy.mockRestore();
  });

  it('renders plain img for non-protected image URLs', () => {
    render(<MiniMd text={`![图标](https://example.com/icon.png)`} />);
    const img = screen.getByRole('img', { name: '图标' }) as HTMLImageElement;
    expect(img.src).toContain('https://example.com/icon.png');
    expect(apiFetchBlobMock).not.toHaveBeenCalled();
  });
});
