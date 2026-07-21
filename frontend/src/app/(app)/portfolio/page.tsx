'use client';

import { Box } from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { portfolioApi } from '@/lib/api/endpoints';
import { PageHeader } from '@/components/common';
import { PortfolioKpis } from '@/components/portfolio/PortfolioKpis';
import { PortfolioHistoryChart } from '@/components/portfolio/PortfolioHistoryChart';
import { AllocationDonut } from '@/components/portfolio/AllocationDonut';
import { PositionsTable } from '@/components/portfolio/PositionsTable';
import { TradesTable } from '@/components/portfolio/TradesTable';

export default function PortfolioPage() {
  const {
    data: portfolio,
    isLoading: portfolioLoading,
  } = useQuery({
    queryKey: ['portfolio'],
    queryFn: portfolioApi.get,
    refetchInterval: 15_000,
  });

  const {
    data: positions,
    isLoading: positionsLoading,
  } = useQuery({
    queryKey: ['portfolio', 'positions'],
    queryFn: portfolioApi.positions,
    refetchInterval: 15_000,
  });

  const {
    data: trades,
    isLoading: tradesLoading,
  } = useQuery({
    queryKey: ['portfolio', 'trades', 50],
    queryFn: () => portfolioApi.trades(50),
    refetchInterval: 30_000,
  });

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      <PageHeader
        title="Portefeuille"
        subtitle="Vue d'ensemble du capital, positions ouvertes et historique des trades"
      />

      {/* Row 1 — KPI StatCards */}
      <PortfolioKpis portfolio={portfolio} isLoading={portfolioLoading} />

      {/* Row 2 — History chart (2fr) + Allocation donut (1fr) */}
      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: { xs: '1fr', lg: '2fr 1fr' },
          alignItems: 'stretch',
        }}
      >
        <PortfolioHistoryChart />
        <AllocationDonut positions={positions} isLoading={positionsLoading} />
      </Box>

      {/* Row 3 — Open positions table */}
      <PositionsTable positions={positions} isLoading={positionsLoading} />

      {/* Row 4 — Trade history table */}
      <TradesTable trades={trades} isLoading={tradesLoading} />
    </Box>
  );
}
