'use client';
import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box, Stack } from '@mui/material';
import { PageHeader } from '@/components/common';
import { portfolioApi, tradingApi, riskApi, systemsApi } from '@/lib/api/endpoints';
import { useEventSubscription } from '@/lib/ws/WebSocketProvider';
import { KpiTicker } from '@/components/command/KpiTicker';
import { PipelineFlow } from '@/components/systems/PipelineFlow';
import { LiveEventStream } from '@/components/command/LiveEventStream';
import { DecisionTraceDrawer } from '@/components/command/DecisionTraceDrawer';
import { AiDecisionFeed } from '@/components/command/AiDecisionFeed';
import { FunnelPanel } from '@/components/command/FunnelPanel';
import { LivePnlPanel } from '@/components/command/LivePnlPanel';
import { MarketHeatPanel } from '@/components/command/MarketHeatPanel';
import { GuardrailPanel } from '@/components/command/GuardrailPanel';
import { HealthRail } from '@/components/command/HealthRail';

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

export default function CommandCenterPage() {
  const eventsPerMin = useEventsPerMin();
  const [traceCid, setTraceCid] = useState<string | null>(null);
  const portfolio = useQuery({ queryKey: ['portfolio'], queryFn: portfolioApi.get, refetchInterval: 6000 });
  const status = useQuery({ queryKey: ['trading', 'status'], queryFn: tradingApi.status, refetchInterval: 10000 });
  const exposure = useQuery({ queryKey: ['risk', 'exposure'], queryFn: riskApi.exposure, refetchInterval: 8000 });
  const systems = useQuery({ queryKey: ['systems', 'overview'], queryFn: systemsApi.overview, refetchInterval: 8000 });

  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <PageHeader title="Command Center" subtitle="Supervision temps réel — décisions, exécution, marché & santé" />
      <KpiTicker portfolio={portfolio.data} status={status.data} exposure={exposure.data} eventsPerMin={eventsPerMin} />
      <Box sx={{ mt: 2, display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: '2fr 1fr' }, alignItems: 'start' }}>
        <Stack spacing={2}>
          <Box className="cmi-glass reveal" sx={{ borderRadius: 3, p: 2 }}>
            {systems.data && <PipelineFlow stages={systems.data.pipeline} />}
          </Box>
          <LiveEventStream onSelect={setTraceCid} />
        </Stack>
        <Stack spacing={2}>
          <AiDecisionFeed />
          <FunnelPanel />
          <GuardrailPanel />
        </Stack>
      </Box>
      <Box sx={{ mt: 2, display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
        <LivePnlPanel />
        <MarketHeatPanel />
      </Box>
      <Box sx={{ mt: 2 }}><HealthRail /></Box>
      <DecisionTraceDrawer correlationId={traceCid} onClose={() => setTraceCid(null)} />
    </Box>
  );
}
