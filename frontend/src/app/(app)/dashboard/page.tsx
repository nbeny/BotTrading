'use client';

import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box, Card, CardContent, Chip, Stack, Typography } from '@mui/material';
import SpeedIcon from '@mui/icons-material/Speed';

import { portfolioApi, marketApi, tradingApi, riskApi } from '@/lib/api/endpoints';
import { useEventSubscription } from '@/lib/ws/WebSocketProvider';
import { LiveFeed } from '@/components/realtime/LiveFeed';
import { PageHeader } from '@/components/common';

import { KpiRow } from '@/components/dashboard/KpiRow';
import { PortfolioChart } from '@/components/dashboard/PortfolioChart';
import { SignalsTable } from '@/components/dashboard/SignalsTable';
import { RiskAlerts } from '@/components/dashboard/RiskAlerts';
import { TradesTable } from '@/components/dashboard/TradesTable';

// ─── Evénements/min counter ─────────────────────────────────────────────────
function useEventsPerMin() {
  const timestamps = useRef<number[]>([]);
  const [rate, setRate] = useState(0);

  useEventSubscription([], () => {
    const now = Date.now();
    timestamps.current = [...timestamps.current, now].filter((t) => now - t < 60_000);
    setRate(timestamps.current.length);
  });

  return rate;
}

// ─── Dashboard page ──────────────────────────────────────────────────────────
export default function DashboardPage() {
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 5_000);
    return () => clearInterval(id);
  }, []);

  const eventsPerMin = useEventsPerMin();

  // Queries
  const portfolioQuery = useQuery({
    queryKey: ['portfolio'],
    queryFn: portfolioApi.get,
    refetchInterval: 6_000,
  });

  const positionsQuery = useQuery({
    queryKey: ['positions'],
    queryFn: portfolioApi.positions,
    refetchInterval: 6_000,
  });

  const historyQuery = useQuery({
    queryKey: ['portfolio', 'history', '30d'],
    queryFn: () => portfolioApi.history('30d'),
    refetchInterval: 60_000,
  });

  const tradesQuery = useQuery({
    queryKey: ['trades', 10],
    queryFn: () => portfolioApi.trades(10),
    refetchInterval: 8_000,
  });

  const signalsQuery = useQuery({
    queryKey: ['signals'],
    queryFn: () => marketApi.signals(20),
    refetchInterval: 7_000,
  });

  const opportunitiesQuery = useQuery({
    queryKey: ['opportunities'],
    queryFn: () => tradingApi.opportunities('all'),
    refetchInterval: 8_000,
  });

  const alertsQuery = useQuery({
    queryKey: ['risk', 'alerts'],
    queryFn: () => riskApi.alerts(20),
    refetchInterval: 5_000,
  });

  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <PageHeader
        title="Tableau de Bord"
        subtitle="Vue temps réel du terminal CMI"
        actions={
          <Stack direction="row" spacing={1} alignItems="center">
            <SpeedIcon sx={{ fontSize: 18, color: 'text.secondary' }} />
            <Chip
              label={`${eventsPerMin} événements/min`}
              size="small"
              variant="outlined"
              color={eventsPerMin > 0 ? 'success' : 'default'}
              className="mono"
            />
          </Stack>
        }
      />

      {/* KPI Row */}
      <KpiRow
        portfolio={portfolioQuery.data}
        positions={positionsQuery.data}
        isLoading={portfolioQuery.isLoading || positionsQuery.isLoading}
      />

      {/* Main two-column layout */}
      <Box
        sx={{
          display: 'grid',
          gap: 2,
          mt: 2,
          gridTemplateColumns: { xs: '1fr', lg: '2fr 1fr' },
          alignItems: 'start',
        }}
      >
        {/* LEFT: main content column */}
        <Stack spacing={2}>
          {/* Portfolio area chart */}
          <PortfolioChart data={historyQuery.data} isLoading={historyQuery.isLoading} />

          {/* Signals + Opportunities */}
          <SignalsTable
            signals={signalsQuery.data}
            opportunities={opportunitiesQuery.data}
            isLoadingSignals={signalsQuery.isLoading}
            isLoadingOpportunities={opportunitiesQuery.isLoading}
            now={now}
          />

          {/* Trade history */}
          <TradesTable trades={tradesQuery.data} isLoading={tradesQuery.isLoading} />
        </Stack>

        {/* RIGHT: side column */}
        <Stack spacing={2}>
          {/* Risk Alerts */}
          <RiskAlerts alerts={alertsQuery.data} isLoading={alertsQuery.isLoading} now={now} />

          {/* Live WebSocket feed */}
          <LiveFeed height={520} title="Flux Temps Réel" />

          {/* Last updated info */}
          {portfolioQuery.dataUpdatedAt > 0 && (
            <Card>
              <CardContent sx={{ py: '12px !important' }}>
                <Typography variant="caption" color="text.secondary">
                  Dernière mise à jour :{' '}
                  <span className="mono">
                    {new Date(portfolioQuery.dataUpdatedAt).toLocaleTimeString('fr-FR')}
                  </span>
                </Typography>
              </CardContent>
            </Card>
          )}
        </Stack>
      </Box>
    </Box>
  );
}
