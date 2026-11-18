import { beforeEach, describe, expect, it, vi } from 'vitest';
import { uploadVisualEvidence, type VisualEvidenceSourceType } from './visual-context';

const fetchMock = vi.fn();
function readBytes(file: Blob): Promise<number[]> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(Array.from(new Uint8Array(reader.result as ArrayBuffer)));
    reader.onerror = reject;
    reader.readAsArrayBuffer(file);
  });
}

describe('visual evidence multipart contract (#2177)', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    fetchMock.mockReset().mockResolvedValue({ ok: true, status: 200, text: async () => '{"evidence_id":"evidence"}' });
  });
  it.each<VisualEvidenceSourceType>(['reference_upload', 'map_viewport', 'map_region'])(
    'preserves %s bytes and metadata without setting a boundary', async (sourceType) => {
      const bytes = [137, 80, 78, 71, 0, 255];
      const file = new File([new Uint8Array(bytes)], 'evidence.png', { type: 'image/png' });
      const crop = { x: 1, y: 2, w: 100, h: 90 };
      const viewport = { center: [110, 30], zoom: 7, bearing: 0, pitch: 0 };
      await expect(uploadVisualEvidence({
        sessionId: 'session/a', file, sourceType, ownerToken: 'owner-test',
        label: 'map evidence', mapspecRevision: 3, mapspecFingerprint: 'fingerprint',
        viewport, crop, dpr: 2,
      })).resolves.toEqual({ evidence_id: 'evidence' });
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
      expect(new URL(url).pathname).toBe('/api/v1/chat/sessions/session%2Fa/visual-context/evidence');
      expect(init.method).toBe('POST');
      const headers = new Headers(init.headers);
      expect(headers.get('X-Session-Token')).toBe('owner-test');
      expect(headers.has('Content-Type')).toBe(false);
      expect(init.body).toBeInstanceOf(FormData);
      const form = init.body as FormData;
      const uploaded = form.get('image') as File;
      expect(uploaded.name).toBe(sourceType === 'reference_upload' ? 'evidence.png' : 'capture.png');
      expect(uploaded.type).toBe('image/png');
      expect(await readBytes(uploaded)).toEqual(bytes);
      expect(form.get('source_type')).toBe(sourceType);
      expect(form.get('label')).toBe('map evidence');
      if (sourceType === 'reference_upload') {
        expect(Array.from(form.keys())).toEqual(['image', 'source_type', 'label']);
      } else {
        expect(form.get('mapspec_revision')).toBe('3');
        expect(form.get('mapspec_fingerprint')).toBe('fingerprint');
        expect(form.get('viewport')).toBe(JSON.stringify(viewport));
        expect(form.get('dpr')).toBe('2');
        expect(form.get('crop')).toBe(sourceType === 'map_region' ? JSON.stringify(crop) : null);
      }
    },
  );
});
