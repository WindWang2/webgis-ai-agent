import { useEffect, useRef, useCallback, useState } from 'react';
import { useHudStore, type HudState } from '@/lib/store/useHudStore';
import { WS_BASE } from '@/lib/api/config';

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

export function useWebSocket(sessionId?: string) {
  const socketRef = useRef<WebSocket | null>(null);
  const retryCountRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [connected, setConnected] = useState(false);

  const connect = useCallback(() => {
    if (!sessionId) return;

    const { addProcessLayer, removeProcessLayer } = useHudStore.getState();

    const url = `${WS_URL}/${sessionId}`;
    const socket = new WebSocket(url);
    socketRef.current = socket;

    socket.onopen = () => {
      retryCountRef.current = 0;
      setConnected(true);
    };

    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        const { event: eventType, data } = payload;

        if (eventType === 'STEP_COMPLETED' || eventType === 'geojson_update') {
          if (data.step_id && data.geojson) {
            addProcessLayer(data.step_id, data.geojson);
          }
        } else if (eventType === 'STEP_REMOVED') {
          if (data.step_id) {
            removeProcessLayer(data.step_id);
          }
        }
      } catch {}
    };

    socket.onclose = () => {
      setConnected(false);
      socketRef.current = null;
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
