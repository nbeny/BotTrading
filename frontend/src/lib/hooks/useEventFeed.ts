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

  // `types` and `symbol` are sent to the server for the archived pages, but the
  // socket subscription is deliberately unfiltered (`[]` = every type) because
  // one provider feeds the whole app. Applying the same filter to live frames
  // here is what makes the option mean one thing: without it, a caller asking
  // for DecisionEvent would still watch PriceEvents stream past, and would have
  // to re-filter `items` itself to undo the surprise.
  const wanted = useMemo(
    () => new Set(opts.types?.split(',').map((t) => t.trim()).filter(Boolean) ?? []),
    [opts.types],
  );
  const wantedSymbol = opts.symbol?.toUpperCase();

  useEventSubscription([], (event: CmiEvent, msg) => {
    const id = event.event_id;
    if (!id) return;
    if (wanted.size && !wanted.has(event.event_type)) return;
    if (wantedSymbol && event.symbol?.toUpperCase() !== wantedSymbol) return;
    if (!remember(id)) return;
    setLive((prev) =>
      [
        {
          time: event.occurred_at ?? new Date().toISOString(),
          event_id: id,
          event_type: event.event_type,
          // The socket envelope carries the Kafka topic; take it rather than
          // leaving it blank. Otherwise the same event shows an empty topic
          // while live and the real one once it comes back from the archive,
          // which reads as a rendering bug in the feed.
          topic: msg.topic,
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

  // Frames accumulated under the previous filter no longer match this one, and
  // react-query swaps to a different cache entry for the archive half. Dropping
  // them keeps the two halves describing the same query instead of leaving the
  // old category's events stranded at the top of the feed.
  useEffect(() => {
    setLive([]);
    seen.current.clear();
    seenOrder.current = [];
  }, [opts.types, opts.symbol]);

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
