'use client';

import { useEffect, useState } from 'react';
import { Box, Card, CardContent, Skeleton, Typography } from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api/endpoints';
import { PageHeader } from '@/components/common';
import { LiveEventStream } from '@/components/command/LiveEventStream';
import { TokensTable } from '@/components/market/TokensTable';
import { TokenPricePanel } from '@/components/market/TokenPricePanel';
import { WorkerDecisionsPanel } from '@/components/market/WorkerDecisionsPanel';
import { NewsPanel } from '@/components/market/NewsPanel';

export default function MarketPage() {
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  // Tick relative times every 30s
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const { data: tokens = [], isLoading: tokensLoading } = useQuery({
    queryKey: ['market', 'tokens'],
    queryFn: marketApi.tokens,
    refetchInterval: 30_000,
  });

  const { data: news = [], isLoading: newsLoading } = useQuery({
    queryKey: ['market', 'news'],
    queryFn: () => marketApi.news(20),
    refetchInterval: 60_000,
  });

  const { data: decisions = [], isLoading: decisionsLoading } = useQuery({
    queryKey: ['market', 'decisions'],
    queryFn: () => marketApi.decisions(30),
    refetchInterval: 30_000,
  });

  // Auto-select the first token once loaded
  useEffect(() => {
    if (!selectedSymbol && tokens.length > 0) {
      setSelectedSymbol(tokens[0].symbol);
    }
  }, [tokens, selectedSymbol]);

  const selectedToken = tokens.find((t) => t.symbol === selectedSymbol) ?? null;

  return (
    <Box>
      <PageHeader
        title="Intelligence de marché"
        subtitle="Tokens, prix, news et décisions des workers Claude en temps réel"
      />

      {/* ── Tokens table — full width ─────────────────────────────────────── */}
      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" sx={{ mb: 2 }}>
            Tokens surveillés
          </Typography>
          <TokensTable
            tokens={tokens}
            loading={tokensLoading}
            selectedSymbol={selectedSymbol}
            onSelect={setSelectedSymbol}
          />
        </CardContent>
      </Card>

      {/* ── Middle row: chart+stats | decisions ──────────────────────────── */}
      <Box
        sx={{
          display: 'grid',
          gap: 3,
          gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' },
          mb: 3,
          alignItems: 'start',
        }}
      >
        {/* Left: price chart for selected token */}
        {tokensLoading ? (
          <Skeleton variant="rectangular" height={420} sx={{ borderRadius: 2 }} />
        ) : selectedToken ? (
          <TokenPricePanel token={selectedToken} />
        ) : null}

        {/* Right: worker decisions */}
        <Box sx={{ overflowY: 'auto', maxHeight: 520 }}>
          <WorkerDecisionsPanel
            decisions={decisions}
            loading={decisionsLoading}
            now={now}
          />
        </Box>
      </Box>

      {/* ── Bottom row: news | live feed ─────────────────────────────────── */}
      {/* `alignItems: start` so the feed keeps its own height: the news column
          is far taller, and a stretched feed card would be mostly empty space
          below the pagination control. */}
      <Box
        sx={{
          display: 'grid',
          gap: 3,
          gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' },
          alignItems: 'start',
        }}
      >
        <NewsPanel news={news} loading={newsLoading} now={now} />
        {/* Wrapped: SectionCard sets `height: 100%`, which resolves against the
            grid area and would stretch the feed to the height of the news
            column. The intermediate Box is auto-height, so the card keeps its
            own size. No trace drawer here, so no `onSelect` — rows stay
            read-only. */}
        <Box>
          <LiveEventStream
            height={420}
            title="Analyses IA en direct"
            subtitle="Historique archivé + flux temps réel"
          />
        </Box>
      </Box>
    </Box>
  );
}
