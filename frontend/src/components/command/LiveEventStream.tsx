'use client';

/**
 * The single event feed of the terminal — Command Center *and* market page.
 *
 * Merged from the two components that used to exist (`command/LiveEventStream`
 * and `realtime/LiveFeed`): both rendered the same bus, from the same socket,
 * with different labels for the same `event_type`. Keeping them apart meant
 * every change to the event vocabulary had to be made twice, and they had
 * already drifted.
 *
 * Backed by `useEventFeed`, so the archive is shown alongside the live stream:
 * opening the page no longer starts from an empty list.
 */

import { useEffect, useMemo, useState } from 'react';
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { SectionCard } from '@/components/systems/common';
import { useEventFeed } from '@/lib/hooks/useEventFeed';
import { fmtPct, fmtRelative, fmtUsdCompact } from '@/lib/format';
import type { ArchivedEvent } from '@/lib/types/events';

type ChipColor = 'default' | 'primary' | 'secondary' | 'success' | 'warning' | 'error' | 'info';

/**
 * Keyed on the `event_type` values the backend actually publishes — the
 * `EventType` enum of `libs/cmi_common/cmi_common/events/base.py`.
 *
 * `OrderExecutedEvent` is *not* one of them: the trading-engine emits
 * `ExecutionEvent` (with a `kind` discriminating submitted/filled/closed/…).
 * The frontend type of that name only ever matched the mock simulator, which
 * still emits it, so both are mapped here — production shows `ExecutionEvent`,
 * `NEXT_PUBLIC_USE_MOCK=1` shows `OrderExecutedEvent`, and neither renders a
 * raw class name.
 *
 * `PositionChangedEvent` / `PortfolioChangedEvent` are deliberately absent:
 * no service publishes them, they exist only in the frontend types as mock
 * leftovers, and labelling them suggested a stream that does not exist.
 */
const TYPE_META: Record<string, { label: string; color: ChipColor }> = {
  PriceEvent: { label: 'PRIX', color: 'info' },
  VolumeEvent: { label: 'VOLUME', color: 'info' },
  DexEvent: { label: 'DEX', color: 'info' },
  NewsEvent: { label: 'NEWS', color: 'default' },
  SocialEvent: { label: 'SOCIAL', color: 'default' },
  SentimentEvent: { label: 'SENTIMENT', color: 'secondary' },
  AnalysisEvent: { label: 'HAIKU', color: 'warning' },
  DecisionEvent: { label: 'SONNET', color: 'primary' },
  RiskApprovedEvent: { label: 'RISQUE OK', color: 'success' },
  RiskRejectedEvent: { label: 'RISQUE KO', color: 'error' },
  ExecutionEvent: { label: 'EXÉCUTION', color: 'success' },
  OrderExecutedEvent: { label: 'ORDRE', color: 'success' },
};

interface Filter {
  key: string;
  label: string;
  types: string[];
}

/**
 * Every category lists types a producer really emits, so no tab can be
 * structurally empty. `RiskRejectedEvent` rides the `decision.events` topic but
 * belongs with execution: it is the refusal side of the same gate.
 */
const FILTERS: Filter[] = [
  { key: 'all', label: 'Tout', types: [] },
  { key: 'ia', label: 'IA', types: ['AnalysisEvent', 'DecisionEvent'] },
  {
    key: 'exec',
    label: 'Exécution',
    types: ['RiskApprovedEvent', 'RiskRejectedEvent', 'ExecutionEvent', 'OrderExecutedEvent'],
  },
  {
    key: 'market',
    label: 'Marché',
    types: ['PriceEvent', 'VolumeEvent', 'DexEvent', 'SentimentEvent', 'NewsEvent', 'SocialEvent'],
  },
];

function num(v: unknown): number | null {
  const n = typeof v === 'string' ? Number(v) : typeof v === 'number' ? v : NaN;
  return Number.isFinite(n) ? n : null;
}

function text(v: unknown): string | null {
  return typeof v === 'string' && v.trim() ? v.trim() : null;
}

function clip(s: string, max = 64): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

function join(parts: (string | null)[]): string {
  return parts.filter(Boolean).join(' · ');
}

/**
 * One-line gist of an event, read off the archived `payload` (the same JSON the
 * socket delivers). Unknown types render as an empty summary rather than a
 * guess — the type chip and the symbol already say what happened.
 */
function summarize(e: ArchivedEvent): string {
  const p = e.payload ?? {};
  switch (e.event_type) {
    case 'PriceEvent': {
      const price = num(p.price_usd);
      const chg = num(p.price_change_pct_24h);
      return join([price != null ? `$${price.toLocaleString('en-US')}` : null, chg != null ? fmtPct(chg) : null]);
    }
    case 'VolumeEvent': {
      const spike = num(p.volume_spike_ratio);
      return join([
        num(p.volume_24h_usd) != null ? fmtUsdCompact(num(p.volume_24h_usd)) : null,
        spike != null ? `spike ×${spike}` : null,
      ]);
    }
    case 'DexEvent':
      return join([text(p.dex), num(p.liquidity_usd) != null ? `liq. ${fmtUsdCompact(num(p.liquidity_usd))}` : null]);
    case 'NewsEvent':
      return clip(text(p.title) ?? '');
    case 'SocialEvent':
      return join([text(p.platform), num(p.mentions) != null ? `${num(p.mentions)} mentions` : null]);
    case 'SentimentEvent':
      return join([
        num(p.sentiment_score) != null ? `sentiment ${num(p.sentiment_score)}` : null,
        text(p.input_kind),
      ]);
    case 'AnalysisEvent':
      return join([
        num(p.opportunity_score) != null ? `score ${num(p.opportunity_score)}` : null,
        p.escalate === true ? 'escalade Sonnet' : null,
        text(p.reason) ? clip(text(p.reason)!, 48) : null,
      ]);
    case 'DecisionEvent':
      return join([
        text(p.direction)?.toUpperCase() ?? null,
        p.ai_validated === true ? 'validé IA' : 'candidat',
        text(p.rationale) ? clip(text(p.rationale)!, 48) : null,
      ]);
    case 'RiskApprovedEvent':
      return join([
        num(p.entry_price) != null ? `entrée ${num(p.entry_price)}` : null,
        num(p.stop_loss) != null ? `SL ${num(p.stop_loss)}` : null,
        num(p.take_profit) != null ? `TP ${num(p.take_profit)}` : null,
        num(p.risk_reward_ratio) != null ? `R/R ${num(p.risk_reward_ratio)}` : null,
      ]);
    case 'RiskRejectedEvent':
      return text(p.reason) ? clip(text(p.reason)!) : 'refusé';
    case 'ExecutionEvent':
      return join([
        text(p.kind),
        num(p.fill_price) != null ? `@ ${num(p.fill_price)}` : null,
        num(p.size) != null ? `taille ${num(p.size)}` : null,
      ]);
    case 'OrderExecutedEvent':
      return join([
        text(p.side)?.toUpperCase() ?? null,
        num(p.volume) != null && num(p.price) != null ? `${num(p.volume)} @ ${num(p.price)}` : null,
        text(p.mode),
      ]);
    default:
      return '';
  }
}

export function LiveEventStream({
  onSelect,
  height = 340,
  title = 'Flux temps réel',
  subtitle = 'Clique un événement → sa lignée décisionnelle',
}: {
  /** Omit on pages with no trace drawer: rows then render non-clickable. */
  onSelect?: (cid: string) => void;
  /** Height of the scrolling list, in px. */
  height?: number;
  title?: string;
  subtitle?: string;
}) {
  const [filterKey, setFilterKey] = useState('all');
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const active = FILTERS.find((f) => f.key === filterKey) ?? FILTERS[0];

  // One filter, applied in one place. `types` goes into the query key, so a
  // category change refetches and "charger plus" then walks the *matching*
  // history instead of paging through rows the client would throw away;
  // `useEventFeed` applies the same list to the live frames it keeps. Filtering
  // again here would only hide a divergence between the two halves.
  const types = active.types.length ? active.types.join(',') : undefined;
  const { items: visible, fetchNextPage, hasNextPage, isFetchingNextPage, isLoading } =
    useEventFeed({ types });

  const empty = visible.length === 0;

  return (
    <SectionCard
      title={title}
      subtitle={subtitle}
      noPad
      actions={
        <ToggleButtonGroup size="small" exclusive value={filterKey} onChange={(_, v) => v && setFilterKey(v)}>
          {FILTERS.map((f) => (
            <ToggleButton key={f.key} value={f.key} sx={{ px: 1.25, py: 0.25, fontSize: 11 }}>
              {f.label}
            </ToggleButton>
          ))}
        </ToggleButtonGroup>
      }
    >
      <Box sx={{ height, overflowY: 'auto' }}>
        {empty ? (
          <Stack alignItems="center" spacing={1.5} sx={{ p: 4 }}>
            {isLoading && <CircularProgress size={18} />}
            <Typography variant="body2" color="text.secondary" align="center">
              {isLoading ? "Chargement de l'historique…" : "Rien pour l'instant — le flux s'affiche dès qu'un événement arrive."}
            </Typography>
          </Stack>
        ) : (
          visible.map((e) => {
            const meta = TYPE_META[e.event_type] ?? { label: e.event_type, color: 'default' as ChipColor };
            const gist = summarize(e);
            const traceable = Boolean(e.correlation_id && onSelect);
            return (
              <Stack
                key={e.event_id}
                direction="row"
                justifyContent="space-between"
                alignItems="center"
                spacing={1}
                className="flash-up"
                onClick={() => traceable && onSelect!(e.correlation_id!)}
                sx={{
                  px: 2,
                  py: 0.75,
                  cursor: traceable ? 'pointer' : 'default',
                  borderBottom: '1px solid',
                  borderColor: 'divider',
                  '&:hover': { bgcolor: traceable ? 'rgba(77,159,255,0.08)' : undefined },
                }}
              >
                <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0 }}>
                  <Chip
                    size="small"
                    color={meta.color}
                    label={meta.label}
                    sx={{ height: 18, fontSize: 9, flexShrink: 0 }}
                  />
                  <Typography variant="body2" sx={{ fontWeight: 600, flexShrink: 0 }}>
                    {e.symbol ?? '—'}
                  </Typography>
                  {gist && (
                    <Typography variant="caption" color="text.secondary" noWrap sx={{ minWidth: 0 }}>
                      {gist}
                    </Typography>
                  )}
                </Stack>
                <Stack direction="row" spacing={1} alignItems="center" sx={{ flexShrink: 0 }}>
                  <Typography variant="caption" className="mono" color="text.secondary">
                    {fmtRelative(e.time, now)}
                  </Typography>
                  {traceable && (
                    <Typography variant="caption" color="primary.main">
                      trace →
                    </Typography>
                  )}
                </Stack>
              </Stack>
            );
          })
        )}
      </Box>
      <Box sx={{ px: 2, py: 1, borderTop: '1px solid', borderColor: 'divider', textAlign: 'center' }}>
        <Button
          size="small"
          onClick={() => void fetchNextPage()}
          disabled={!hasNextPage || isFetchingNextPage}
          startIcon={isFetchingNextPage ? <CircularProgress size={12} /> : undefined}
        >
          {isFetchingNextPage ? 'Chargement…' : hasNextPage ? 'Charger plus' : "Fin de l'historique"}
        </Button>
      </Box>
    </SectionCard>
  );
}
