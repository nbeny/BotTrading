'use client';

import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  useCallback,
} from 'react';
import { WS_URL, USE_MOCK, ACCESS_TOKEN_KEY } from '@/lib/config';
import type { CmiEvent, WsMessage } from '@/lib/types/events';

export type ConnStatus = 'connecting' | 'open' | 'closed';
type Listener = (msg: WsMessage) => void;

interface WsContextValue {
  status: ConnStatus;
  /** Subscribe to every inbound event; returns an unsubscribe fn. */
  subscribe: (fn: Listener) => () => void;
  /** Rolling in-memory feed of the most recent events (newest first). */
  feed: WsMessage[];
  lastEventAt: number | null;
}

const WsContext = createContext<WsContextValue | null>(null);

const MAX_FEED = 200;

/**
 * Real-time transport. In mock mode it drives a synthetic Kafka-shaped stream;
 * otherwise it connects to the WebSocket gateway (Kafka -> WS -> Next.js) with
 * auto-reconnect and exponential backoff.
 */
export function WebSocketProvider({ children }: { children: React.ReactNode }) {
  const [status, setStatus] = useState<ConnStatus>('connecting');
  const [feed, setFeed] = useState<WsMessage[]>([]);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const listeners = useRef<Set<Listener>>(new Set());

  const dispatch = useCallback((msg: WsMessage) => {
    setLastEventAt(Date.now());
    setFeed((prev) => [msg, ...prev].slice(0, MAX_FEED));
    listeners.current.forEach((fn) => {
      try {
        fn(msg);
      } catch {
        /* isolate listener errors */
      }
    });
  }, []);

  const subscribe = useCallback((fn: Listener) => {
    listeners.current.add(fn);
    return () => {
      listeners.current.delete(fn);
    };
  }, []);

  useEffect(() => {
    let disposed = false;
    let ws: WebSocket | null = null;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout> | null = null;

    if (USE_MOCK) {
      // Consume the server-side simulation: an incremental poll of
      // /api/mock/stream/recent. `since=0` on first load returns backfilled
      // history, so the feed is populated instantly and survives refreshes.
      setStatus('open');
      let cursor = 0;
      const poll = async () => {
        if (disposed) return;
        try {
          const r = await fetch(`/api/mock/stream/recent?since=${cursor}&limit=150`);
          const data = (await r.json()) as {
            cursor: number;
            events: { topic: string; event: CmiEvent; ts: string }[];
          };
          cursor = data.cursor ?? cursor;
          // dispatch oldest→newest so the feed ends up newest-first
          for (const m of data.events) dispatch({ topic: m.topic, event: m.event, ts: m.ts });
          setStatus('open');
        } catch {
          setStatus('connecting');
        }
        if (!disposed) timer = setTimeout(poll, 1300);
      };
      poll();
      return () => {
        disposed = true;
        if (timer) clearTimeout(timer);
      };
    }

    const connect = () => {
      if (disposed) return;
      setStatus('connecting');
      const token =
        typeof window !== 'undefined'
          ? window.localStorage.getItem(ACCESS_TOKEN_KEY)
          : null;
      const url = token ? `${WS_URL}?token=${encodeURIComponent(token)}` : WS_URL;
      ws = new WebSocket(url);

      ws.onopen = () => {
        retry = 0;
        setStatus('open');
      };
      ws.onmessage = (ev) => {
        try {
          const parsed = JSON.parse(ev.data) as WsMessage;
          if (parsed?.event) dispatch(parsed);
        } catch {
          /* ignore malformed frames */
        }
      };
      ws.onclose = () => {
        setStatus('closed');
        if (disposed) return;
        retry += 1;
        const delay = Math.min(30_000, 1000 * 2 ** Math.min(retry, 5));
        timer = setTimeout(connect, delay);
      };
      ws.onerror = () => ws?.close();
    };

    connect();
    return () => {
      disposed = true;
      if (timer) clearTimeout(timer);
      ws?.close();
    };
  }, [dispatch]);

  const value = useMemo<WsContextValue>(
    () => ({ status, subscribe, feed, lastEventAt }),
    [status, subscribe, feed, lastEventAt],
  );

  return <WsContext.Provider value={value}>{children}</WsContext.Provider>;
}

export function useWebSocket(): WsContextValue {
  const ctx = useContext(WsContext);
  if (!ctx) throw new Error('useWebSocket must be used within WebSocketProvider');
  return ctx;
}

/** Subscribe to events of specific types with an always-fresh callback. */
export function useEventSubscription(
  types: CmiEvent['event_type'][],
  handler: (event: CmiEvent, msg: WsMessage) => void,
) {
  const { subscribe } = useWebSocket();
  const ref = useRef(handler);
  ref.current = handler;
  const key = types.join(',');
  useEffect(() => {
    const set = new Set(key.split(',').filter(Boolean));
    return subscribe((msg) => {
      if (set.size === 0 || set.has(msg.event.event_type)) {
        ref.current(msg.event, msg);
      }
    });
  }, [subscribe, key]);
}
