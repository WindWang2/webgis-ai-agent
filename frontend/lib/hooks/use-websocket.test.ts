import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useWebSocket } from './use-websocket';
import { useHudStore } from '@/lib/store/useHudStore';
import type { HudState } from '@/lib/store/useHudStore';

class MockWebSocket {
  static OPEN = 1;
  static CLOSED = 3;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn(() => {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.();
  });

  simulateOpen() {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.();
  }

  simulateMessage(data: object) {
    this.onmessage?.({ data: JSON.stringify(data) });
  }
}

let mockSocketInstance: MockWebSocket;

const WebSocketMock = vi.fn(() => mockSocketInstance) as ReturnType<typeof vi.fn> & {
  OPEN: number;
  CLOSED: number;
};
WebSocketMock.OPEN = 1;
WebSocketMock.CLOSED = 3;

vi.stubGlobal('WebSocket', WebSocketMock);

vi.mock('@/lib/api/config', () => ({
  WS_BASE: 'ws://localhost:8000',
}));

describe('useWebSocket', () => {
  const addProcessLayer = vi.fn();
  const removeProcessLayer = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    vi.useFakeTimers();
    mockSocketInstance = new MockWebSocket();
    WebSocketMock.mockReturnValue(mockSocketInstance);
    // Re-attach static properties after clearAllMocks
    WebSocketMock.OPEN = 1;
    WebSocketMock.CLOSED = 3;
    vi.spyOn(useHudStore, 'getState').mockReturnValue({
      addProcessLayer,
      removeProcessLayer,
      drainPerception: vi.fn(() => []),
      pushPerception: vi.fn(),
      viewport: { center: [116.4, 39.9] as [number, number], zoom: 10, bearing: 0, pitch: 0 },
      layers: [],
      baseLayer: 'Carto Dark',
      is3D: false,
    } as unknown as HudState);
    vi.spyOn(useHudStore, 'subscribe').mockReturnValue(() => {});
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('does not connect when sessionId is undefined', () => {
    renderHook(() => useWebSocket());
    expect(WebSocket).not.toHaveBeenCalled();
  });

  it('creates WebSocket connection with correct URL', () => {
    renderHook(() => useWebSocket('session-123'));
    expect(WebSocket).toHaveBeenCalledWith('ws://localhost:8000/api/v1/ws/session-123');
  });

  it('sets connected=true on open', () => {
    const { result } = renderHook(() => useWebSocket('s1'));
    act(() => { mockSocketInstance.simulateOpen(); });
    expect(result.current.connected).toBe(true);
  });

  it('sets connected=false on close', () => {
    const { result } = renderHook(() => useWebSocket('s1'));
    act(() => { mockSocketInstance.simulateOpen(); });
    expect(result.current.connected).toBe(true);
    act(() => { mockSocketInstance.close(); });
    expect(result.current.connected).toBe(false);
  });

  it('calls addProcessLayer on STEP_COMPLETED event', () => {
    renderHook(() => useWebSocket('s1'));
    act(() => { mockSocketInstance.simulateOpen(); });
    act(() => {
      mockSocketInstance.simulateMessage({
        event: 'STEP_COMPLETED',
        data: { step_id: 'step-1', geojson: { type: 'FeatureCollection', features: [] } },
      });
    });
    expect(addProcessLayer).toHaveBeenCalledWith('step-1', { type: 'FeatureCollection', features: [] });
  });

  it('calls removeProcessLayer on STEP_REMOVED event', () => {
    renderHook(() => useWebSocket('s1'));
    act(() => { mockSocketInstance.simulateOpen(); });
    act(() => {
      mockSocketInstance.simulateMessage({
        event: 'STEP_REMOVED',
        data: { step_id: 'step-1' },
      });
    });
    expect(removeProcessLayer).toHaveBeenCalledWith('step-1');
  });

  it('sendMessage sends JSON via socket when open', () => {
    const { result } = renderHook(() => useWebSocket('s1'));
    act(() => { mockSocketInstance.simulateOpen(); });
    act(() => { result.current.sendMessage({ event: 'test', data: {} }); });
    expect(mockSocketInstance.send).toHaveBeenCalled();
  });

  it('sendMessage does nothing when socket is not open', () => {
    const { result } = renderHook(() => useWebSocket('s1'));
    // Don't simulate open
    act(() => { result.current.sendMessage({ event: 'test' }); });
    expect(mockSocketInstance.send).not.toHaveBeenCalled();
  });
});
