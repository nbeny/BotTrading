'use client';

import { useEffect, useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Chip,
  Stack,
  Typography,
  ToggleButton,
  ToggleButtonGroup,
} from '@mui/material';
import { useWebSocket } from '@/lib/ws/WebSocketProvider';
import type { CmiEvent, EventType, WsMessage } from '@/lib/types/events';
import { fmtRelative } from '@/lib/format';

const TYPE_META: Record<
  string,
  { label: string; color: 'default' | 'primary' | 'secondary' | 'success' | 'warning' | 'error' | 'info' }
> = {
  PriceEvent: { label: 'Prix', color: 'info' },
  VolumeEvent: { label: 'Volume', color: 'info' },
  DexEvent: { label: 'DEX', color: 'info' },
  NewsEvent: { label: 'News', color: 'default' },
  SocialEvent: { label: 'Social', color: 'default' },
  SentimentEvent: { label: 'Sentiment', color: 'secondary' },
  AnalysisEvent: { label: 'Haiku', color: 'warning' },
  DecisionEvent: { label: 'Sonnet', color: 'primary' },
  RiskApprovedEvent: { label: 'Risk OK', color: 'success' },
  OrderExecutedEvent: { label: 'Ordre', color: 'success' },
  PositionChangedEvent: { label: 'Position', color: 'info' },
  PortfolioChangedEvent: { label: 'Portfolio', color: 'secondary' },
};

function describe(e: CmiEvent): string {
  switch (e.event_type) {
    case 'PriceEvent':
      return `${e.symbol} @ $${Number(e.price_usd).toLocaleString()} (${e.price_change_pct_24h > 0 ? '+' : ''}${e.price_change_pct_24h}%)`;
    case 'SentimentEvent':
      return `${e.symbol} sentiment ${e.sentiment_score} · ${e.input_kind}`;
    case 'AnalysisEvent':
      return `${e.symbol} score ${e.opportunity_score} — ${e.reason}${e.escalate ? ' ⟶ escalade Sonnet' : ''}`;
    case 'DecisionEvent':
      return `${e.symbol} ${e.direction.toUpperCase()} · ${e.ai_validated ? 'validé IA' : 'candidat'} — ${e.rationale.slice(0, 70)}…`;
    case 'RiskApprovedEvent':
      return `${e.symbol} approuvé · entrée ${e.entry_price} SL ${e.stop_loss} TP ${e.take_profit} (RR ${e.risk_reward_ratio})`;
    case 'OrderExecutedEvent':
      return `${e.side.toUpperCase()} ${e.symbol} ${e.volume} @ ${e.price} [${e.mode}]`;
    case 'PositionChangedEvent':
      return `${e.symbol} position ${e.change} · qty ${e.quantity}`;
    case 'PortfolioChangedEvent':
      return `Portefeuille $${e.total_value_usd.toLocaleString()} (${e.pnl_24h_pct > 0 ? '+' : ''}${e.pnl_24h_pct}% / 24h)`;
    default:
      return e.symbol;
  }
}

const FILTERS: { key: string; label: string; types: EventType[] }[] = [
  { key: 'all', label: 'Tout', types: [] },
  { key: 'ia', label: 'IA', types: ['AnalysisEvent', 'DecisionEvent'] },
  { key: 'exec', label: 'Exécution', types: ['RiskApprovedEvent', 'OrderExecutedEvent', 'PositionChangedEvent'] },
  { key: 'market', label: 'Marché', types: ['PriceEvent', 'SentimentEvent', 'VolumeEvent', 'DexEvent'] },
];

export function LiveFeed({ height = 520, title = 'Flux temps réel' }: { height?: number; title?: string }) {
  const { feed } = useWebSocket();
  const [filter, setFilter] = useState('all');
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  const active = FILTERS.find((f) => f.key === filter)!;
  const items: WsMessage[] =
    active.types.length === 0
      ? feed
      : feed.filter((m) => active.types.includes(m.event.event_type));

  return (
    <Card sx={{ display: 'flex', flexDirection: 'column', height }}>
      <CardContent sx={{ pb: 1 }}>
        <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1}>
          <Typography variant="h6">{title}</Typography>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={filter}
            onChange={(_, v) => v && setFilter(v)}
          >
            {FILTERS.map((f) => (
              <ToggleButton key={f.key} value={f.key} sx={{ px: 1.5, py: 0.25, fontSize: 12 }}>
                {f.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Stack>
      </CardContent>
      <Box sx={{ flex: 1, overflowY: 'auto', px: 2, pb: 2 }}>
        {items.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            En attente d&apos;événements…
          </Typography>
        ) : (
          <Stack spacing={0.5}>
            {items.map((m, i) => {
              const meta = TYPE_META[m.event.event_type] ?? { label: m.event.event_type, color: 'default' as const };
              return (
                <Stack
                  key={`${m.event.event_id ?? i}-${i}`}
                  direction="row"
                  spacing={1.5}
                  alignItems="center"
                  sx={{
                    py: 0.75,
                    px: 1,
                    borderRadius: 1.5,
                    borderBottom: '1px solid rgba(255,255,255,0.04)',
                  }}
                >
                  <Chip label={meta.label} color={meta.color} size="small" sx={{ minWidth: 78 }} />
                  <Typography variant="body2" sx={{ flex: 1, fontSize: 13 }}>
                    {describe(m.event)}
                  </Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ whiteSpace: 'nowrap' }} className="mono">
                    {fmtRelative(m.ts, now)}
                  </Typography>
                </Stack>
              );
            })}
          </Stack>
        )}
      </Box>
    </Card>
  );
}
