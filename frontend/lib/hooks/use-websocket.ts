import { useEffect, useRef, useCallback, useState } from 'react';
import { useHudStore, type HudState } from '@/lib/store/useHudStore';
import { WS_BASE, API_BASE } from '@/lib/api/config';

const WS_URL = `${WS_BASE}/api/v1/ws`;

function enrichLayerMeta(l: any) {
  return {
    id: l.id,
    name: l.name,
    type: l.type,
    visible: l.visible,
    opacity: l.opacity,
    group: l.group,
    featureCount: l.source && typeof l.source === 'object' && 'features' in l.source
      ? (l.source as any).features?.length || 0 : undefined,
    style: l.style,
  };
}

const MAX_RECONNECT_ATTEMPTS = 10;

export function useWebSocket(sessionId?: string) {
  const socketRef = useRef<WebSocket | null>(null);
  const retryCountRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [connected, setConnected] = useState(false);
  const sessionIdRef = useRef<string | undefined>(sessionId);
  sessionIdRef.current = sessionId;

  const connect = useCallback(() => {
    if (!sessionId) return;

    const { addProcessLayer, removeProcessLayer } = useHudStore.getState();

    const url = `${WS_URL}/${sessionId}`;
    const socket = new WebSocket(url);
    socketRef.current = socket;

    socket.onopen = () => {
      retryCountRef.current = 0;
      setConnected(true);
      // Start heartbeat
      const pingInterval = setInterval(() => {
        if (socket.readyState === WebSocket.OPEN) {
          socket.send(JSON.stringify({ event: 'ping' }));
        }
      }, 30000);
      (socket as any)._pingInterval = pingInterval;
    };

    socket.onmessage = async (event) => {
      try {
        const payload = JSON.parse(event.data);
        const { event: eventType, data } = payload;

        if (eventType === 'pong') return;

        if (eventType === 'STEP_COMPLETED' || eventType === 'geojson_update') {
          if (data.step_id && data.geojson) {
            let geojson = data.geojson;
            // If it's a reference, fetch the actual data
            if (typeof geojson === 'string' && geojson.startsWith('ref:')) {
              try {
                const resp = await fetch(`${API_BASE}/api/v1/layers/data/${geojson}?session_id=${sessionId}`);
                if (resp.ok) {
                  geojson = await resp.json();
                } else {
                  console.error('Failed to fetch geojson ref:', geojson);
                  return;
                }
              } catch (e) {
                console.error('Error fetching geojson ref:', e);
                return;
              }
            }
            addProcessLayer(data.step_id, geojson);
          }
        } else if (eventType === 'STEP_REMOVED') {
          if (data.step_id) {
            removeProcessLayer(data.step_id);
          }
        }
      } catch (e) {
        console.error('[WS] failed to parse message:', e, event.data);
      }
    };

    socket.onclose = () => {
      if ((socket as any)._pingInterval) clearInterval((socket as any)._pingInterval);
      setConnected(false);
      socketRef.current = null;
      // 守卫：当前没有可用 session_id 时不应该尝试重连
      if (!sessionIdRef.current) return;
      // 守卫：超过最大重试次数则停止（避免无限循环消耗资源）
      if (retryCountRef.current >= MAX_RECONNECT_ATTEMPTS) {
        console.warn(`[WS] reached max reconnect attempts (${MAX_RECONNECT_ATTEMPTS}), giving up`);
        return;
      }
      const delay = Math.min(1000 * Math.pow(2, retryCountRef.current), 30000);
      retryCountRef.current += 1;
      timerRef.current = setTimeout(connect, delay);
    };

    socket.onerror = () => {
      socket.close();
    };
  }, [sessionId]);

  useEffect(() => {
    connect();

    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
      if (socketRef.current) {
        socketRef.current.onclose = null;
        socketRef.current.close();
        socketRef.current = null;
      }
      setConnected(false);
    };
  }, [connect]);

  const sendMessage = useCallback((message: any) => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify(message));
    }
  }, []);

  // ─── Perception Buffer Flush (300ms) ───
  useEffect(() => {
    const interval = setInterval(() => {
      const events = useHudStore.getState().drainPerception();
      if (events.length > 0 && socketRef.current?.readyState === WebSocket.OPEN) {
        for (const evt of events) {
          socketRef.current.send(JSON.stringify(evt));
        }
      }
    }, 300);
    return () => clearInterval(interval);
  }, []);

  // ─── Zustand Subscription: viewport + layer changes → perception ───
  useEffect(() => {
    const unsub = useHudStore.subscribe((state: HudState, prevState: HudState) => {
      // Viewport change
      if (state.viewport !== prevState.viewport) {
        useHudStore.getState().pushPerception('viewport_change', {
          center: state.viewport.center,
          zoom: state.viewport.zoom,
          bearing: state.viewport.bearing,
          pitch: state.viewport.pitch,
        });
      }
      // Layer list structural change
      if (state.layers !== prevState.layers) {
        useHudStore.getState().pushPerception('layers_changed', {
          layers: state.layers.map(enrichLayerMeta),
        });
      }
      // Base layer change
      if (state.baseLayer !== prevState.baseLayer) {
        useHudStore.getState().pushPerception('base_layer_changed', {
          name: state.baseLayer,
        });
      }
      // 3D mode change
      if (state.is3D !== prevState.is3D) {
        useHudStore.getState().pushPerception('mode_changed', {
          is_3d: state.is3D,
        });
      }
    });
    return unsub;
  }, []);

  // ─── Periodic Full State Snapshot (30s) ───
  useEffect(() => {
    const interval = setInterval(() => {
      const state = useHudStore.getState();
      if (socketRef.current?.readyState !== WebSocket.OPEN) return;
      socketRef.current.send(JSON.stringify({
        event: 'state_snapshot',
        data: {
          viewport: state.viewport,
          base_layer: state.baseLayer,
          is_3d: state.is3D,
          layers: state.layers.map(enrichLayerMeta),
        },
      }));
    }, 30000);
    return () => clearInterval(interval);
  }, []);

  return { sendMessage, connected };
}
