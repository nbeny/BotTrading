'use client';
import { useRef, useState } from 'react';
import { Stack, Typography, Chip, Box } from '@mui/material';
import { SectionCard } from '@/components/systems/common';
import { useEventSubscription } from '@/lib/ws/WebSocketProvider';
import { useDecisionParam } from '@/lib/hooks/useDecisionParam';
import type { AnalysisEvent, DecisionEvent, CmiEvent } from '@/lib/types/events';

type Item = { id: string; symbol: string; worker: 'Haiku' | 'Sonnet'; dir?: 'long' | 'short'; conf: number; score: number };

export function AiDecisionFeed() {
  const [items, setItems] = useState<Item[]>([]);
  const buf = useRef<Item[]>([]);
  const { open } = useDecisionParam();
  useEventSubscription(['AnalysisEvent', 'DecisionEvent'], (e: CmiEvent) => {
    const it: Item = e.event_type === 'DecisionEvent'
      ? { id: e.event_id!, symbol: e.symbol, worker: 'Sonnet', dir: (e as DecisionEvent).direction, conf: (e as DecisionEvent).confidence, score: (e as DecisionEvent).opportunity_score }
      : { id: e.event_id!, symbol: e.symbol, worker: 'Haiku', conf: (e as AnalysisEvent).confidence, score: (e as AnalysisEvent).opportunity_score };
    buf.current = [it, ...buf.current].slice(0, 20); setItems([...buf.current]);
  });
  return (
    <SectionCard title="Fil décisions IA" subtitle="Haiku triage · Sonnet senior" accent="#a78bfa" noPad>
      <Box sx={{ maxHeight: 300, overflowY: 'auto' }}>
        {items.map((it) => (
          <Stack
            key={it.id}
            direction="row"
            justifyContent="space-between"
            alignItems="center"
            onClick={() => open(it.id)}
            sx={{ px: 2, py: 0.75, borderBottom: '1px solid', borderColor: 'divider', cursor: 'pointer' }}
          >
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="body2" sx={{ fontWeight: 700 }}>{it.symbol}</Typography>
              <Chip size="small" label={it.worker} sx={{ height: 18, fontSize: 9, bgcolor: 'rgba(167,139,250,0.16)', color: '#d3c4ff' }} />
            </Stack>
            <Chip size="small" label={it.dir ? `${it.dir.toUpperCase()} ${Math.round(it.conf * 100)}%` : `score ${it.score}`}
              color={it.dir === 'long' ? 'success' : it.dir === 'short' ? 'error' : 'default'} variant={it.dir ? 'filled' : 'outlined'} sx={{ height: 20, fontSize: 10 }} />
          </Stack>
        ))}
        {!items.length && <Typography variant="body2" color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>En attente…</Typography>}
      </Box>
    </SectionCard>
  );
}
