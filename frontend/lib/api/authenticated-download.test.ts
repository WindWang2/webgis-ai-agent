import { describe, it, expect, vi, beforeEach } from 'vitest';
import { API_BASE } from './config';
import {
  isProtectedDownloadUrl,
  filenameFromUrl,
  downloadWithAuth,
} from './authenticated-download';

// #515 regression: bare <a href> navigation / <img src> on the export and
// report download routes can never attach the Bearer header → hard 401.
// These helpers route the fetch through the transport (auth + 401 refresh).
const apiFetchBlobMock = vi.fn();
vi.mock('./transport', () => ({
  apiFetchBlob: (...args: unknown[]) => apiFetchBlobMock(...args),
}));

function exportUrl(name: string): string {
  return `${API_BASE}/api/v1/export/download/${name}`;
}

describe('isProtectedDownloadUrl', () => {
  it('matches export download URLs', () => {
    expect(isProtectedDownloadUrl(exportUrl('map_export_abc123.png'))).toBe(true);
  });

  it('matches report download URLs', () => {
    expect(isProtectedDownloadUrl(`${API_BASE}/api/v1/reports/report-id/download`)).toBe(true);
  });

  it('rejects third-party / non-download URLs', () => {
    expect(isProtectedDownloadUrl('https://tile.openstreetmap.org/1/2/3.png')).toBe(false);
    expect(isProtectedDownloadUrl(`${API_BASE}/api/v1/export`)).toBe(false);
    expect(isProtectedDownloadUrl(`${API_BASE}/api/v1/export/download/`)).toBe(true); // still the download path
    expect(isProtectedDownloadUrl('')).toBe(false);
  });

  it('rejects URLs outside API_BASE even with a download-looking path', () => {
    expect(isProtectedDownloadUrl('https://evil.example/api/v1/export/download/x.png')).toBe(false);
  });
});

describe('filenameFromUrl', () => {
  it('extracts the last path segment', () => {
    expect(filenameFromUrl(exportUrl('a.png'))).toBe('a.png');
    expect(filenameFromUrl(`${API_BASE}/api/v1/reports/rep-1/download`)).toBe('download');
  });

  it('strips query strings and decodes', () => {
    expect(filenameFromUrl(`${exportUrl('a%20b.png')}?session_id=x`)).toBe('a b.png');
  });
});

describe('downloadWithAuth', () => {
  beforeEach(() => {
    apiFetchBlobMock.mockReset();
    apiFetchBlobMock.mockResolvedValue({ blob: new Blob(['data']), filename: null });
  });

  it('fetches the blob through the transport and triggers a browser download', async () => {
    const url = exportUrl('map_export_1.png');
    // Capture the anchor the trigger creates so we can assert its attributes.
    let captured: HTMLAnchorElement | null = null;
    const appendSpy = vi.spyOn(document.body, 'appendChild').mockImplementation(((el: any) => {
      captured = el;
      return el;
    }) as any);
    const removeSpy = vi.spyOn(document.body, 'removeChild').mockImplementation(((el: any) => el) as any);
    const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-1');
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    await downloadWithAuth(url);

    // transport called with the path (API_BASE stripped) + auth options
    expect(apiFetchBlobMock).toHaveBeenCalledWith(`/api/v1/export/download/map_export_1.png`, expect.anything());
    expect(createSpy).toHaveBeenCalled();
    expect(revokeSpy).toHaveBeenCalled();
    expect(captured).not.toBeNull();
    expect(captured!.download).toBe('map_export_1.png');
    expect(captured!.href).toContain('blob:mock-1');

    appendSpy.mockRestore();
    removeSpy.mockRestore();
    createSpy.mockRestore();
    revokeSpy.mockRestore();
  });

  it('uses the Content-Disposition filename when the server provides one', async () => {
    apiFetchBlobMock.mockResolvedValue({
      blob: new Blob(['data']),
      filename: 'server-name.pdf',
    });
    let downloadAttr: string | null = null;
    vi.spyOn(document.body, 'appendChild').mockImplementation(((a: any) => {
      downloadAttr = a.download ?? null;
      return a;
    }) as any);
    vi.spyOn(document.body, 'removeChild').mockImplementation(((a: any) => a) as any);
    vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    await downloadWithAuth(exportUrl('fallback.png'));
    expect(downloadAttr).toBe('server-name.pdf');
    vi.restoreAllMocks();
  });

  it('propagates transport errors (401 → caller surfaces login requirement)', async () => {
    apiFetchBlobMock.mockRejectedValue(new Error('401'));
    await expect(downloadWithAuth(exportUrl('x.png'))).rejects.toThrow('401');
  });
});
