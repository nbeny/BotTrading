'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useInfiniteQuery } from '@tanstack/react-query';
import { eventsApi } from '@/lib/api/endpoints';
import { useEventSubscription } from '@/lib/ws/WebSocketProvider';
import type { ArchivedEvent, CmiEvent } from '@/lib/types/events';

/** Newest live frames kept in memory; older ones are already in the archive. */
const MAX_LIVE = 500;
/** Cap on remembered ids — see `remember()`; eviction is cosmetic, not lossy. */
const MAX_SEEN = 5_000;

/** Milliseconds for sorting. `time` is ISO-8601 but the offset spelling differs
 * between sources (`...Z` from the socket, `...+00:00` from Python), so a plain
 * string compare would mis-order events sharing the same second. */
function ts(e: ArchivedEvent): number {
  const t = Date.parse(e.time);
  return Number.isNaN(t) ? 0 : t;
}

/**
 * Combines the archived history with the live WebSocket stream.
 *
 * Deduplicated by `event_id`: an event can legitimately arrive twice — once on
 * the socket and once in a history page fetched moments later — and showing it
 * twice would make the feed look like it was double-counting.
 *
 * Must be used inside `WebSocketProvider` (mounted in `components/providers/
 * Providers.tsx`, so it covers the whole app tree).
 *
 * The WebSocketProvider stays a pure transport; nothing here is pushed back
 * into it.
 */
export function useEventFeed(opts: { types?: string; symbol?: string } = {}) {
  const query = useInfiniteQuery({
    queryKey: ['events', opts.types ?? null, opts.symbol ?? null],
    initialPageParam: null as string | null,
    queryFn: ({ pageParam }) =>
      eventsApi.page({ limit: 100, before: pageParam, types: opts.types, symbol: opts.symbol }),
    getNextPageParam: (last) => last.next_cursor,
  });

  const [live, setLive] = useState<ArchivedEvent[]>([]);
  const seen = useRef<Set<string>>(new Set());
  const seenOrder = useRef<string[]>([]);

  /**
   * Registers an id, returning false if it was already known. Bounded so a
   * terminal left open for days does not accumulate ids without limit; evicting
   * the oldest is safe because `items` below dedupes by id anyway — the set only
   * keeps the `live` array from growing with copies.
   */
  const remember = useCallback((id: string) => {
    if (seen.current.has(id)) return false;
    seen.current.add(id);
    seenOrder.current.push(id);
    if (seenOrder.current.length > MAX_SEEN) {
      for (const old of seenOrder.current.splice(0, seenOrder.current.length - MAX_SEEN)) {
        seen.current.delete(old);
      }
    }
    return true;
  }, []);

  useEventSubscription([], (event: CmiEvent) => {
    const id = event.event_id;
    if (!id || !remember(id)) return;
    setLive((prev) =>
      [
        {
          time: event.occurred_at ?? new Date().toISOString(),
          event_id: id,
          event_type: event.event_type,
          topic: '',
          symbol: (event as { symbol?: string }).symbol ?? null,
          correlation_id: event.correlation_id ?? null,
          payload: event as unknown as Record<string, unknown>,
        },
        ...prev,
      ].slice(0, MAX_LIVE),
    );
  });

  const items = useMemo(() => {
    const merged = new Map<string, ArchivedEvent>();
    for (const e of live) merged.set(e.event_id, e);
    for (const page of query.data?.pages ?? []) {
      for (const e of page.items) if (!merged.has(e.event_id)) merged.set(e.event_id, e);
    }
    return [...merged.values()].sort((a, b) => ts(b) - ts(a));
  }, [live, query.data]);

  // A history page can contain an event the socket already delivered; register
  // those ids so a later frame for the same event is not appended again.
  useEffect(() => {
    for (const page of query.data?.pages ?? []) {
      for (const e of page.items) remember(e.event_id);
    }
  }, [query.data, remember]);

  return {
    items,
    fetchNextPage: query.fetchNextPage,
    hasNextPage: query.hasNextPage,
    isFetchingNextPage: query.isFetchingNextPage,
    isLoading: query.isLoading,
  };
}
