/**
 * #610 regression: uploadFile's XHR channel must carry the same credential
 * headers as the rest of the transport — Authorization Bearer for signed-in
 * sessions (the backend upload route depends on get_current_user) plus the
 * anonymous-session X-Session-Token when an owner token is present.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { uploadFile } from './upload';
import { clearAuth, setAuth } from '../auth/tokenStore';

/** Minimal XHR double: records open/headers/send, lets the test drive onload. */
class FakeXHR {
  static instances: FakeXHR[] = [];
  headers: Record<string, string> = {};
  status = 0;
  responseText = '';
  timeout = 0;
  upload = { onprogress: null as null | ((e: unknown) => void) };
  onload: null | (() => void) = null;
  onerror: null | (() => void) = null;
  ontimeout: null | (() => void) = null;

  constructor() {
    FakeXHR.instances.push(this);
  }

  open(): void {}
  setRequestHeader(key: string, value: string): void {
    this.headers[key] = value;
  }
  send(): void {}
  abort(): void {}
}

function makeFile(): File {
  return new File(['{"type":"Point"}'], 'test.geojson', {
    type: 'application/geo+json',
  });
}

async function driveSuccess(xhr: FakeXHR): Promise<void> {
  xhr.status = 200;
  xhr.responseText = JSON.stringify({
    id: 1,
    original_name: 'test.geojson',
    file_type: 'vector',
    format: 'geojson',
    crs: 'EPSG:4326',
    geometry_type: 'Point',
    feature_count: 1,
    bbox: [0, 0, 1, 1],
    file_size: 12,
  });
  xhr.onload?.();
}

afterEach(() => {
  clearAuth();
  vi.unstubAllGlobals();
  FakeXHR.instances.length = 0;
});

describe('uploadFile auth headers', () => {
  it('attaches Authorization Bearer when signed in (#610)', async () => {
    setAuth({ accessToken: 'jwt-upload-1', refreshToken: 'jwt-ref' }, null);
    vi.stubGlobal('XMLHttpRequest', FakeXHR);

    const promise = uploadFile(makeFile(), 'sess-1', undefined, { ownerToken: 'owner-1' });
    const xhr = FakeXHR.instances[0];
    await driveSuccess(xhr);
    await promise;

    expect(xhr.headers['Authorization']).toBe('Bearer jwt-upload-1');
    expect(xhr.headers['X-Session-Token']).toBe('owner-1');
  });

  it('attaches no Authorization header when signed out', async () => {
    clearAuth();
    vi.stubGlobal('XMLHttpRequest', FakeXHR);

    const promise = uploadFile(makeFile(), 'sess-anon', undefined, { ownerToken: 'owner-anon' });
    const xhr = FakeXHR.instances[0];
    await driveSuccess(xhr);
    await promise;

    expect(xhr.headers['Authorization']).toBeUndefined();
    expect(xhr.headers['X-Session-Token']).toBe('owner-anon');
    expect(xhr.headers['X-Request-ID']).toBeTruthy();
  });

  it('422 detail arrays surface msg text', async () => {
    vi.stubGlobal('XMLHttpRequest', FakeXHR);
    const promise = uploadFile(makeFile());
    const xhr = FakeXHR.instances[0];
    xhr.status = 422;
    xhr.responseText = JSON.stringify({
      detail: [{ loc: ['body', 'files'], msg: 'field required', type: 'missing' }],
    });
    xhr.onload?.();
    await expect(promise).rejects.toThrow('field required');
  });

  it('retries once after 401 when refresh succeeds', async () => {
    setAuth({ accessToken: 'old', refreshToken: 'ref' }, null);
    vi.stubGlobal('XMLHttpRequest', FakeXHR);
    vi.stubGlobal('fetch', vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ access_token: 'fresh', refresh_token: 'ref-2' }),
    })));
    const promise = uploadFile(makeFile());
    const first = FakeXHR.instances[0];
    first.status = 401;
    first.responseText = JSON.stringify({ detail: 'unauthorized' });
    first.onload?.();
    await vi.waitFor(() => expect(FakeXHR.instances.length).toBe(2));
    expect(FakeXHR.instances[1].headers.Authorization).toBe('Bearer fresh');
    await driveSuccess(FakeXHR.instances[1]);
    await expect(promise).resolves.toMatchObject({ id: 1 });
  });
});