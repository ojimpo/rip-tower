import { useEffect, useRef, useCallback, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import type { WsEvent } from "../lib/types";

const WS_URL = `ws://${window.location.host}/ws`;
const RECONNECT_DELAY = 3000;

export function useWebSocket(onEvent?: (event: WsEvent) => void) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const queryClient = useQueryClient();

  const handleEvent = useCallback(
    (event: WsEvent) => {
      // Invalidate relevant react-query caches based on event type
      switch (event.type) {
        case "job:status":
        case "job:progress":
        case "job:track_done":
          queryClient.invalidateQueries({ queryKey: ["jobs"] });
          queryClient.invalidateQueries({ queryKey: ["job", event.job_id] });
          break;
        case "job:complete":
          queryClient.invalidateQueries({ queryKey: ["jobs"] });
          queryClient.invalidateQueries({ queryKey: ["job", event.job_id] });
          queryClient.invalidateQueries({ queryKey: ["history"] });
          queryClient.invalidateQueries({ queryKey: ["stats"] });
          break;
        case "job:error":
        case "job:review":
          queryClient.invalidateQueries({ queryKey: ["jobs"] });
          queryClient.invalidateQueries({ queryKey: ["job", event.job_id] });
          break;
        case "drive:connected":
        case "drive:disconnected":
        case "drive:disc_inserted":
        case "drive:disc_ejected":
          queryClient.invalidateQueries({ queryKey: ["drives"] });
          break;
        case "job:gnudb_submitted":
          queryClient.invalidateQueries({ queryKey: ["gnudb", event.job_id] });
          queryClient.invalidateQueries({ queryKey: ["job", event.job_id] });
          break;
      }

      // Call custom handler if provided
      onEvent?.(event);
    },
    [queryClient, onEvent]
  );

  const connect = useCallback(() => {
    const ws = new WebSocket(WS_URL);

    ws.onopen = () => setConnected(true);

    ws.onmessage = (e) => {
      try {
        const event = JSON.parse(e.data) as WsEvent;
        handleEvent(event);
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
  }, [handleEvent]);

  useEffect(() => {
    connect();
    return () => wsRef.current?.close();
  }, [connect]);

  return { connected };
}
