import { useEffect, useRef, useCallback } from 'react';
import { useHudStore } from '@/lib/store/useHudStore';
import { WS_BASE } from '../api/config';

const WS_URL = `${WS_BASE}/api/v1/ws`;

export function useWebSocket(sessionId?: string) {
  const socketRef = useRef<WebSocket | null>(null);
  const { addProcessLayer, removeProcessLayer } = useHudStore();

  useEffect(() => {
    if (!sessionId) return;

    const url = `${WS_URL}/${sessionId}`;
    const socket = new WebSocket(url);
    socketRef.current = socket;

    socket.onopen = () => {
      console.log(`[WS] Connected to ${url}`);
    };

    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data);
        const { event: eventType, data } = payload;

        if (eventType === 'STEP_COMPLETED' || eventType === 'geojson_update') {
          // data should contain { step_id, geojson }
          if (data.step_id && data.geojson) {
            addProcessLayer(data.step_id, data.geojson);
          }
        } else if (eventType === 'STEP_REMOVED') {
          if (data.step_id) {
            removeProcessLayer(data.step_id);
          }
        }
      } catch (err) {
        console.error('[WS] Failed to parse message:', err);
      }
    };

    socket.onclose = () => {
      console.log(`[WS] Disconnected from ${url}`);
      socketRef.current = null;
    };

    socket.onerror = (err) => {
      console.error('[WS] Error:', err);
    };

    return () => {
      socket.close();
      socketRef.current = null;
    };
  }, [sessionId, addProcessLayer, removeProcessLayer]);

  const sendMessage = useCallback((message: any) => {
    if (socketRef.current && socketRef.current.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify(message));
    }
  }, []);

  return { sendMessage };
}
