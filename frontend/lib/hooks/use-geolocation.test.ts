import { act, renderHook } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { useGeolocation } from './use-geolocation';

afterEach(() => vi.unstubAllGlobals());

describe('explicit location requests', () => {
  it('never prompts on mount, exposes loading, maps errors and permits retry', () => {
    let success!: PositionCallback;
    let failure!: PositionErrorCallback;
    const request = vi.fn((ok: PositionCallback, error: PositionErrorCallback) => { success = ok; failure = error; });
    vi.stubGlobal('navigator', { geolocation: { getCurrentPosition: request } });
    const { result } = renderHook(() => useGeolocation());
    expect(request).not.toHaveBeenCalled();
    act(() => { result.current.requestLocation(); result.current.requestLocation(); });
    expect(request).toHaveBeenCalledTimes(1);
    expect(result.current.loading).toBe(true);
    act(() => failure({ code: 1 } as GeolocationPositionError));
    expect(result.current).toMatchObject({ loading: false, error: 'denied' });
    act(() => result.current.requestLocation());
    expect(result.current).toMatchObject({ loading: true, error: null });
    act(() => success({ coords: { longitude: 104, latitude: 30, accuracy: 2.3 } } as GeolocationPosition));
    expect(result.current).toMatchObject({ loading: false, error: null, location: { lng: 104, lat: 30, accuracy: 2 } });
  });

  it('exposes unsupported browsers only after an explicit request', () => {
    vi.stubGlobal('navigator', {});
    const { result } = renderHook(() => useGeolocation());
    expect(result.current.error).toBeNull();
    act(() => result.current.requestLocation());
    expect(result.current.error).toBe('unsupported');
  });
});
