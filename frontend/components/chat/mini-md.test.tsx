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

// AuthImage imports the URL guards from first-party (shared with tile-auth).
vi.mock('@/lib/api/first-party', () => ({
  isFirstPartyUrl: (url: string) => typeof url === 'string' && !url.startsWith('https://'),
  toApiPath: (url: string) => {
    const u = new URL(url, 'http://localhost:8000');
    return u.pathname + u.search;
  },
  isProtectedDownloadUrl: (url: string) =>
    typeof url === 'string' && url.includes('/api/v1/export/download/'),
}));

const apiFetchBlobMock = vi.fn();
vi.mock('@/lib/api/transport', () => ({
  apiFetchBlob: (...args: unknown[]) => apiFetchBlobMock(...args),
  describeApiError: (err: unknown, fallback: string) => fallback,
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

describe('MiniMd rich markdown rendering features', () => {
  it('renders rich fenced code blocks with language and copy button', () => {
    const text = '```python\ndef calculate_buffer(geom):\n    return geom.buffer(100)\n```';
    render(<MiniMd text={text} />);

    expect(screen.getByTestId('code-block')).toBeInTheDocument();
    expect(screen.getByTestId('language-pill')).toHaveTextContent('python');
    expect(screen.getByRole('button', { name: /复制/i })).toBeInTheDocument();
    expect(screen.getByText('calculate_buffer')).toBeInTheDocument();
  });

  it('renders inline code with styled badge', () => {
    const text = '使用 `webgis_buffer` 工具处理几何图形。';
    render(<MiniMd text={text} />);

    const inlineCode = screen.getByText('webgis_buffer');
    expect(inlineCode.tagName.toLowerCase()).toBe('code');
    expect(inlineCode.className).toContain('bg-status-accent-soft');
  });

  it('renders tables with headers and rows properly', () => {
    const text = `| 图层名 | 类型 | 要素数量 |
|---|---|---|
| 医院 | Point | 42 |
| 路网 | LineString | 156 |`;

    render(<MiniMd text={text} />);

    expect(screen.getByText('图层名')).toBeInTheDocument();
    expect(screen.getByText('要素数量')).toBeInTheDocument();
    expect(screen.getByText('医院')).toBeInTheDocument();
    expect(screen.getByText('Point')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
  });

  it('renders blockquotes with accent border styling', () => {
    const text = '> 这是一个重要提示信息';
    render(<MiniMd text={text} />);

    const blockquote = screen.getByText(/这是一个重要提示信息/).closest('blockquote');
    expect(blockquote).not.toBeNull();
    expect(blockquote?.className).toContain('border-status-accent');
  });

  it('renders nested ordered and unordered lists', () => {
    const text = `- 步骤一\n- 步骤二\n  1. 子项 A\n  2. 子项 B`;
    render(<MiniMd text={text} />);

    expect(screen.getByText('步骤一')).toBeInTheDocument();
    expect(screen.getByText('步骤二')).toBeInTheDocument();
    expect(screen.getByText('子项 A')).toBeInTheDocument();
  });
});
