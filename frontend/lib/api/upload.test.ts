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
});