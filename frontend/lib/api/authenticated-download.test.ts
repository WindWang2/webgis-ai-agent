import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  isProtectedDownloadUrl,
  filenameFromUrl,
  downloadWithAuth,
} from './authenticated-download';
import { isFirstPartyUrl, toApiPath } from './first-party';

// #515 regression: bare <a href> navigation / <img src> on the export and
// report download routes can never attach the Bearer header → hard 401.
// These helpers route the fetch through the transport (auth + 401 refresh).
const apiFetchBlobMock = vi.fn();
vi.mock('./transport', () => ({
  apiFetchBlob: (...args: unknown[]) => apiFetchBlobMock(...args),
}));

// API_BASE is '' in production and an absolute origin in dev.
let mockApiBase = 'http://localhost:8001';
vi.mock('./config', () => ({
  get API_BASE() {
    return mockApiBase;
  },
}));

function exportUrl(name: string): string {
  return `${mockApiBase}/api/v1/export/download/${name}`;
}

describe('toApiPath', () => {
  it('strips an absolute URL to its origin-relative path + query', () => {
    expect(toApiPath('http://localhost:8001/api/v1/export/download/a.png?x=1')).toBe(
      '/api/v1/export/download/a.png?x=1'
    );
    expect(toApiPath('http://localhost:8001/api/v1/export/download/a.png')).toBe(
      '/api/v1/export/download/a.png'
    );
  });

  it('passes relative URLs through unchanged', () => {
    expect(toApiPath('/api/v1/export/download/a.png')).toBe('/api/v1/export/download/a.png');
    expect(toApiPath('/api/v1/reports/r1/download')).toBe('/api/v1/reports/r1/download');
  });
});

describe('isFirstPartyUrl', () => {
  it('accepts same-origin absolute URLs when a base is configured', () => {
    mockApiBase = 'http://localhost:8001';
    expect(isFirstPartyUrl('http://localhost:8001/api/v1/export/download/a.png')).toBe(true);
  });

  it('rejects hosts that merely share the base origin as a prefix', () => {
    mockApiBase = 'http://api.example.com';
    expect(isFirstPartyUrl('http://api.example.com.evil.com/api/v1/x')).toBe(false);
    expect(isFirstPartyUrl('http://api.example.com/api/v1/x')).toBe(true);
  });

  it('accepts relative paths when API_BASE is empty (production)', () => {
    mockApiBase = '';
    expect(isFirstPartyUrl('/api/v1/export/download/a.png')).toBe(true);
    // protocol-relative URLs are absolute — never treated as first-party
    expect(isFirstPartyUrl('//evil.example.com/api/v1/export/download/a.png')).toBe(false);
  });
});

describe('isProtectedDownloadUrl', () => {
  it('matches export download URLs', () => {
    mockApiBase = 'http://localhost:8001';
    expect(isProtectedDownloadUrl(exportUrl('map_export_abc123.png'))).toBe(true);
  });

  it('matches report download URLs', () => {
    expect(isProtectedDownloadUrl('http://localhost:8001/api/v1/reports/report-id/download')).toBe(true);
  });

  it('matches relative protected paths in the production build (API_BASE === "")', () => {
    mockApiBase = '';
    expect(isProtectedDownloadUrl('/api/v1/export/download/map_export_1.png')).toBe(true);
    expect(isProtectedDownloadUrl('/api/v1/reports/report-id/download')).toBe(true);
    // Relative but not a protected route
    expect(isProtectedDownloadUrl('/api/v1/export')).toBe(false);
    expect(isProtectedDownloadUrl('/api/v1/reports/report-id')).toBe(false);
  });

  it('rejects third-party / foreign-origin URLs even with a protected-looking path', () => {
    mockApiBase = 'http://localhost:8001';
    expect(isProtectedDownloadUrl('https://tile.openstreetmap.org/1/2/3.png')).toBe(false);
    expect(isProtectedDownloadUrl('https://evil.example/api/v1/export/download/x.png')).toBe(false);
    expect(isProtectedDownloadUrl('//evil.example/api/v1/export/download/x.png')).toBe(false);
    expect(isProtectedDownloadUrl('')).toBe(false);
  });

  it('rejects prefix-impersonation hosts', () => {
    mockApiBase = 'http://api.example.com';
    expect(isProtectedDownloadUrl('http://api.example.com.evil.com/api/v1/export/download/x.png')).toBe(false);
  });
});

describe('filenameFromUrl', () => {
  it('extracts the last path segment', () => {
    expect(filenameFromUrl(exportUrl('a.png'))).toBe('a.png');
    expect(filenameFromUrl('http://localhost:8001/api/v1/reports/rep-1/download')).toBe('download');
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

  it('fetches the blob through the transport with the origin-relative path', async () => {
    mockApiBase = 'http://localhost:8001';
    let captured: HTMLAnchorElement | null = null;
    const appendSpy = vi.spyOn(document.body, 'appendChild').mockImplementation(((el: any) => {
      captured = el;
      return el;
    }) as any);
    const removeSpy = vi.spyOn(document.body, 'removeChild').mockImplementation(((el: any) => el) as any);
    const createSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-1');
    const revokeSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    await downloadWithAuth(exportUrl('map_export_1.png'));

    // transport called with the origin-relative path (buildRequest prepends API_BASE)
    expect(apiFetchBlobMock).toHaveBeenCalledWith('/api/v1/export/download/map_export_1.png', expect.anything());
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

  it('downloads relative URLs unchanged in the production build', async () => {
    mockApiBase = '';
    await downloadWithAuth('/api/v1/export/download/map_export_1.png');
    expect(apiFetchBlobMock).toHaveBeenCalledWith('/api/v1/export/download/map_export_1.png', expect.anything());
  });

  it('uses the Content-Disposition filename when the server provides one', async () => {
    apiFetchBlobMock.mockResolvedValue({
      blob: new Blob(['data']),
      filename: 'server-name.pdf',
    });
    let downloadAttr: string | null = null;
    vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:mock-2');
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
