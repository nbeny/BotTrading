'use client';

import { Card, CardContent, Skeleton, Typography } from '@mui/material';
import { PriceAreaChart } from '@/components/charts/PriceAreaChart';
import { EmptyState } from '@/components/common';
import type { PricePoint } from '@/lib/types/domain';

interface PortfolioChartProps {
  data: PricePoint[] | undefined;
  isLoading: boolean;
}

export function PortfolioChart({ data, isLoading }: PortfolioChartProps) {
  return (
    <Card>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Valeur du Portefeuille — 30 jours
        </Typography>
        {isLoading ? (
          <Skeleton variant="rectangular" height={260} sx={{ borderRadius: 2 }} />
        ) : !data || data.length === 0 ? (
          <EmptyState message="Aucune donnée d'historique disponible." />
        ) : (
          <PriceAreaChart data={data} height={260} color="#5b8def" dataKey="price" />
        )}
      </CardContent>
    </Card>
  );
}
