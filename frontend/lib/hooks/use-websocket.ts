import { useEffect, useRef, useCallback, useState } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { WS_BASE } from '@/lib/api/config';

const WS_URL = `${WS_BASE}/api/v1/ws`;

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

  return { sendMessage, connected };
}
