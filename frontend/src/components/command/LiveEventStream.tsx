'use client';
import { useRef, useState } from 'react';
import { Box, Stack, Typography, Chip } from '@mui/material';
import { SectionCard } from '@/components/systems/common';
import { useEventSubscription } from '@/lib/ws/WebSocketProvider';
import type { CmiEvent } from '@/lib/types/events';

const LABEL: Record<string, string> = { PriceEvent: 'PRIX', SentimentEvent: 'SENTIMENT', AnalysisEvent: 'HAIKU', DecisionEvent: 'SONNET', RiskApprovedEvent: 'RISQUE', OrderExecutedEvent: 'ORDRE', PositionChangedEvent: 'POSITION', PortfolioChangedEvent: 'PORTF.' };

export function LiveEventStream({ onSelect }: { onSelect: (cid: string) => void }) {
  const [events, setEvents] = useState<CmiEvent[]>([]);
  const buf = useRef<CmiEvent[]>([]);
  useEventSubscription(
    ['PriceEvent', 'SentimentEvent', 'AnalysisEvent', 'DecisionEvent', 'RiskApprovedEvent', 'OrderExecutedEvent', 'PositionChangedEvent'],
    (e) => { buf.current = [e, ...buf.current].slice(0, 40); setEvents([...buf.current]); });
  return (
    <SectionCard title="Flux temps réel" subtitle="Clique un événement → sa lignée décisionnelle" noPad>
      <Box sx={{ maxHeight: 340, overflowY: 'auto' }}>
        {events.map((e) => (
          <Stack key={e.event_id} direction="row" justifyContent="space-between" alignItems="center" onClick={() => e.correlation_id && onSelect(e.correlation_id)}
            className="flash-up" sx={{ px: 2, py: 0.75, cursor: e.correlation_id ? 'pointer' : 'default', borderBottom: '1px solid', borderColor: 'divider', '&:hover': { bgcolor: 'rgba(77,159,255,0.08)' } }}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="caption" className="mono" color="text.secondary">{new Date(e.occurred_at ?? Date.now()).toLocaleTimeString('fr-FR')}</Typography>
              <Chip size="small" label={LABEL[e.event_type] ?? e.event_type} sx={{ height: 18, fontSize: 9 }} />
              <Typography variant="body2" sx={{ fontWeight: 600 }}>{e.symbol}</Typography>
            </Stack>
            {e.correlation_id && <Typography variant="caption" color="primary.main">trace →</Typography>}
          </Stack>
        ))}
        {!events.length && <Typography variant="body2" color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>En attente d'événements…</Typography>}
      </Box>
    </SectionCard>
  );
}
