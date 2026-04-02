import { useEffect, useRef, useCallback, useState } from "react";
import type { WsEvent } from "../lib/types";

const WS_URL = `ws://${window.location.host}/ws`;
const RECONNECT_DELAY = 3000;

export function useWebSocket(onEvent: (event: WsEvent) => void) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);

  const connect = useCallback(() => {
    const ws = new WebSocket(WS_URL);

    ws.onopen = () => setConnected(true);

    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as WsEvent;
        onEvent(event);
      } catch {
        // ignore malformed messages
      }
    };

    ws.onclose = () => {
      setConnected(false);
      setTimeout(connect, RECONNECT_DELAY);
    };

    ws.onerror = () => ws.close();

    wsRef.current = ws;
  }, [onEvent]);

  useEffect(() => {
    connect();
    return () => wsRef.current?.close();
  }, [connect]);

  return { connected };
}
